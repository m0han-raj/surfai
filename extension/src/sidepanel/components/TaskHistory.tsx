import { Check, X, Ban, Loader2, Clock } from 'lucide-react';
import type { Task } from '@shared/types';

interface TaskHistoryProps {
  tasks: Task[];
  loading: boolean;
  error?: string | null;
  onSelect?: (task: Task) => void;
}

/**
 * Task history.
 *
 * Deliberately a record of *outcomes*: request, site, status, step count,
 * duration and a one-line summary. Intermediate reasoning is never shown.
 */
export default function TaskHistory({ tasks, loading, error, onSelect }: TaskHistoryProps) {
  if (loading) {
    return (
      <div className="section__body">
        <div className="skeleton" style={{ height: 52 }} />
        <div className="skeleton" style={{ height: 52 }} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="banner banner--danger" role="alert">
        <X size={14} aria-hidden="true" />
        <span>{error}</span>
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div className="empty">
        <p className="empty__title">No tasks yet</p>
        <p className="empty__body">
          Completed, failed and cancelled tasks appear here with what SurfAI did.
        </p>
      </div>
    );
  }

  return (
    <div className="section__body">
      {tasks.map((task) => (
        <article key={task.id} className="task">
          <div className="task__header">
            <div className="task__request">{task.request}</div>
            <StatusBadge status={task.status} />
          </div>

          <div className="task__meta">
            {task.current_url && <span>{domainOf(task.current_url)}</span>}
            {typeof (task as Task & { action_count?: number }).action_count === 'number' && (
              <span>{(task as Task & { action_count?: number }).action_count} actions</span>
            )}
            <span>{formatDuration(task)}</span>
            <span>{formatDate(task.created_at)}</span>
          </div>

          {task.summary && <p className="task__summary">{task.summary}</p>}

          {onSelect && (
            <button
              type="button"
              className="button button--ghost button--sm"
              onClick={() => onSelect(task)}
              style={{ marginTop: 'var(--space-2)' }}
            >
              View steps
            </button>
          )}
        </article>
      ))}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { className: string; label: string; Icon: typeof Check }> = {
    COMPLETED: { className: 'status--completed', label: 'Completed', Icon: Check },
    FAILED: { className: 'status--failed', label: 'Failed', Icon: X },
    CANCELLED: { className: 'status--cancelled', label: 'Cancelled', Icon: Ban },
  };
  const entry = map[status] ?? {
    className: 'status--running',
    label: titleCase(status),
    Icon: Loader2,
  };

  return (
    <span className={`status ${entry.className}`}>
      <entry.Icon size={11} aria-hidden="true" />
      {entry.label}
    </span>
  );
}

function titleCase(value: string): string {
  return value.charAt(0) + value.slice(1).toLowerCase();
}

function domainOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url.slice(0, 40);
  }
}

function formatDuration(task: Task): string {
  const withDuration = task as Task & { duration_ms?: number };
  if (typeof withDuration.duration_ms !== 'number') return '';
  const seconds = Math.round(withDuration.duration_ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';

  const now = Date.now();
  const diff = now - date.getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export { Clock };
