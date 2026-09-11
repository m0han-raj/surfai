"""A minimal OpenAI-compatible server for smoke-testing SurfAI without a model.

It is not a language model. It returns *scripted* planner decisions so you can
exercise the full stack -- extension, HTTP, orchestrator, validator, risk gate,
database -- on a machine with no GPU and no model downloaded. Useful for CI, for
a first run-through, and for reproducing agent-loop bugs deterministically.

    python backend/tools/mock_llm_server.py --port 11500

Then point the backend at it:

    LLM_BASE_URL=http://host.docker.internal:11500/v1
    LLM_MODEL=mock-model

Implements the two endpoints the provider uses: GET /v1/models and
POST /v1/chat/completions.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("mock-llm")

MODEL_NAME = "mock-model"


def _extract_ids(prompt: str) -> list[str]:
    """Element ids the prompt offered, in order."""
    seen: list[str] = []
    for match in re.findall(r"\[(e\d+)\]", prompt):
        if match not in seen:
            seen.append(match)
    return seen


def _element_line(prompt: str, element_id: str) -> str:
    match = re.search(rf"\[{re.escape(element_id)}\][^\n]*", prompt)
    return match.group(0) if match else ""


def _find(prompt: str, pattern: str) -> str | None:
    """First element id whose description matches `pattern`."""
    for element_id in _extract_ids(prompt):
        if re.search(pattern, _element_line(prompt, element_id), re.IGNORECASE):
            return element_id
    return None


def _intent_response(prompt: str) -> dict:
    lowered = prompt.lower()
    if re.search(r"\bsave (this|it)\b|save as my|remember this", lowered):
        return {"intent": "save_favourite", "goal": "save the current page"}
    listing = r"\b(my|saved) .{0,30}(favourite|favorite)s?\b.*\blist\b|what have i saved"
    if re.search(listing, lowered):
        return {"intent": "list_favourites", "goal": "list favourites"}
    if re.search(r"\b(open|check|use) my\b", lowered):
        reference = re.search(r"\b(?:open|check|use) my ([a-z0-9 ]{2,40})", lowered)
        return {
            "intent": "use_favourite",
            "favourite_reference": (reference.group(1).strip() if reference else ""),
            "goal": "run the saved task",
        }
    return {"intent": "browse_task", "goal": "act on the current page"}


# Matches only real history lines ("  step 2: CLICK e2 -> ok"), not the word
# "step" as it appears in the system prompt's own instructions.
_HISTORY_LINE = re.compile(r"^\s*step \d+:", re.MULTILINE)


def _planner_response(prompt: str) -> dict:
    """A deterministic search-then-read plan against whatever ids are offered."""
    history = len(_HISTORY_LINE.findall(prompt))

    if "DATA EXTRACTED SO FAR" in prompt:
        return {
            "type": "answer",
            "message": (
                "Here is what I found on the page. (This reply came from the mock LLM "
                "server, not a real model.)"
            ),
        }

    # When the user explicitly asks to buy, propose the purchase control. This
    # exists so the confirmation gate can be exercised without a real model --
    # the backend must still stop and ask before it runs.
    goal = re.search(r"USER GOAL: (.*)", prompt)
    if goal and re.search(r"\b(buy|purchase|order|check ?out)\b", goal.group(1), re.I):
        buy = _find(prompt, r"\b(buy now|place order|checkout|proceed to pay)\b")
        if buy:
            return {
                "type": "action",
                "activity": "Opening checkout",
                "action": {"action": "CLICK", "target": buy},
            }

    search_input = _find(prompt, r"inputType=search|placeholder=\"[^\"]*search")
    search_button = _find(prompt, r"button\s+\"search")

    # Has the query already been typed? Then submit; then read.
    if search_input and 'value="' not in _element_line(prompt, search_input) and history == 0:
        return {
            "type": "action",
            "activity": "Entering search terms",
            "action": {"action": "TYPE", "target": search_input, "value": "laptop"},
        }
    if search_button and history <= 1:
        return {
            "type": "action",
            "activity": "Running search",
            "action": {"action": "CLICK", "target": search_button},
        }

    return {"type": "action", "activity": "Reading page", "action": {"action": "EXTRACT"}}


def _favourite_draft(prompt: str) -> dict:
    name = re.search(r"as (?:my |the )?[\"']?([^\"'.,\n]{2,40})", prompt, re.IGNORECASE)
    return {
        "name": (name.group(1).strip().title() if name else "Saved Page"),
        "intent": "Return to this page for the same task",
        "description": "Created by the mock LLM server",
        "preferences": {},
    }


def build_response(schema_name: str, prompt: str) -> dict:
    """Route by the schema the backend asked for."""
    if "intent" in schema_name:
        return _intent_response(prompt)
    if "planner" in schema_name:
        return _planner_response(prompt)
    if "favourite_draft" in schema_name:
        return _favourite_draft(prompt)
    if "favourite_match" in schema_name:
        return {"favourite_id": "", "confidence": 0.0}
    if "tool_discovery" in schema_name:
        return {"tools": []}
    return {"type": "answer", "message": "Mock response."}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send(
                200,
                {
                    "object": "list",
                    "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "mock"}],
                },
            )
            return
        self._send(404, {"error": {"message": f"Unknown path {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"Unknown path {self.path}"}})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "Body was not valid JSON"}})
            return

        response_format = request.get("response_format") or {}
        schema_name = (
            (response_format.get("json_schema") or {}).get("name")
            if response_format.get("type") == "json_schema"
            else ""
        ) or ""

        messages = request.get("messages") or []
        prompt = "\n".join(str(m.get("content", "")) for m in messages)

        # Without a declared schema, infer from the system prompt.
        if not schema_name:
            if "planning component" in prompt:
                schema_name = "planner_decision"
            elif "classify what a SurfAI user wants" in prompt:
                schema_name = "intent"
            elif "saved favourites" in prompt:
                schema_name = "favourite_draft"

        payload = build_response(schema_name, prompt)
        content = json.dumps(payload)
        logger.debug("schema=%s -> %s", schema_name or "(inferred)", content)

        self._send(
            200,
            {
                "id": f"mock-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.get("model", MODEL_NAME),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 32},
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11500)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log the schema requested and the decision returned, for debugging.",
    )
    args = parser.parse_args()

    parser_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=parser_level, format="%(asctime)s %(message)s")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mock OpenAI-compatible server on http://{args.host}:{args.port}/v1")
    print("This is NOT a language model. It returns scripted decisions.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
