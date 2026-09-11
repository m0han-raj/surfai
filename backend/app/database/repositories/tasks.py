"""Task and task-action persistence, plus the orchestrator's store adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.orchestrator import TERMINAL_STATES, TaskStore
from app.database.database import session_scope
from app.database.models import Task, TaskAction


def _now() -> datetime:
    return datetime.now(UTC)


def action_to_dict(action: TaskAction) -> dict[str, Any]:
    return {
        "id": action.id,
        "task_id": action.task_id,
        "step_number": action.step_number,
        "action_type": action.action_type,
        "target": action.target,
        "arguments": action.arguments or {},
        "result": action.result or {},
        "status": action.status,
        "created_at": action.created_at.isoformat() if action.created_at else None,
    }


def task_to_dict(task: Task, *, include_actions: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": task.id,
        "request": task.request,
        "status": task.status,
        "current_url": task.current_url,
        "summary": task.summary,
        "error": task.error,
        "favourite_id": task.favourite_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }
    if task.created_at and task.completed_at:
        data["duration_ms"] = int(
            (task.completed_at - task.created_at).total_seconds() * 1000
        )
    if include_actions:
        data["actions"] = [action_to_dict(a) for a in task.actions]
        data["action_count"] = len(task.actions)
    return data


class TaskRepository:
    def __init__(self, session: Session, user_id: str = "local-user") -> None:
        self.session = session
        self.user_id = user_id

    def list(self, *, limit: int = 50, offset: int = 0, status: str | None = None) -> list[Task]:
        stmt = select(Task).where(Task.user_id == self.user_id)
        if status:
            stmt = stmt.where(Task.status == status.upper())
        stmt = stmt.order_by(Task.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def get(self, task_id: str) -> Task | None:
        task = self.session.get(Task, task_id)
        if task is None or task.user_id != self.user_id:
            return None
        return task

    def count(self) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(Task).where(Task.user_id == self.user_id)
            )
            or 0
        )

    def create(
        self,
        *,
        task_id: str,
        request: str,
        current_url: str | None = None,
        favourite_id: str | None = None,
        status: str = "ANALYZING",
    ) -> Task:
        task = Task(
            id=task_id,
            user_id=self.user_id,
            request=request,
            status=status,
            current_url=current_url,
            favourite_id=favourite_id,
        )
        self.session.add(task)
        self.session.flush()
        return task

    def update(self, task_id: str, **fields: Any) -> Task | None:
        task = self.get(task_id)
        if task is None:
            return None
        completed = fields.pop("completed", False)
        for key, value in fields.items():
            if value is not None and hasattr(task, key):
                setattr(task, key, value)
        if completed or str(fields.get("status", "")).upper() in TERMINAL_STATES:
            task.completed_at = task.completed_at or _now()
        self.session.flush()
        return task

    def delete(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        self.session.delete(task)
        self.session.flush()
        return True

    def add_action(
        self,
        *,
        task_id: str,
        step_number: int,
        action_type: str,
        target: str | None,
        arguments: dict[str, Any],
        result: dict[str, Any],
        status: str,
    ) -> TaskAction:
        action = TaskAction(
            task_id=task_id,
            step_number=step_number,
            action_type=action_type,
            target=target,
            arguments=arguments,
            result=result,
            status=status,
        )
        self.session.add(action)
        self.session.flush()
        return action


class DatabaseTaskStore(TaskStore):
    """Adapts `TaskRepository` to the orchestrator's persistence hook.

    Each call opens its own short transaction: the orchestrator runs across many
    HTTP round trips, so holding a session open between them would be wrong.
    Persistence failures are logged and swallowed -- losing a history row must
    never abort a task the user is watching.
    """

    def __init__(self, user_id: str = "local-user") -> None:
        self.user_id = user_id

    def create_task(
        self, task_id: str, request: str, url: str, favourite_id: str | None
    ) -> None:
        try:
            with session_scope() as session:
                TaskRepository(session, self.user_id).create(
                    task_id=task_id,
                    request=request,
                    current_url=url,
                    favourite_id=favourite_id,
                )
        except Exception:  # noqa: BLE001
            _log_persistence_failure("create_task", task_id)

    def update_task(self, task_id: str, **fields: Any) -> None:
        try:
            with session_scope() as session:
                TaskRepository(session, self.user_id).update(task_id, **fields)
        except Exception:  # noqa: BLE001
            _log_persistence_failure("update_task", task_id)

    def record_action(
        self,
        task_id: str,
        step: int,
        action: dict[str, Any],
        result: dict[str, Any],
        status: str,
    ) -> None:
        try:
            with session_scope() as session:
                TaskRepository(session, self.user_id).add_action(
                    task_id=task_id,
                    step_number=step,
                    action_type=str(action.get("action", "UNKNOWN")),
                    target=action.get("target"),
                    arguments={k: v for k, v in action.items() if k != "action"},
                    result=result,
                    status=status,
                )
        except Exception:  # noqa: BLE001
            _log_persistence_failure("record_action", task_id)


def _log_persistence_failure(operation: str, task_id: str) -> None:
    import logging

    logging.getLogger(__name__).exception(
        "Task persistence failed during %s for task %s; the task continues", operation, task_id
    )
