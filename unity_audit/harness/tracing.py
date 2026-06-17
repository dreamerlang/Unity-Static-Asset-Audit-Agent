"""TraceWriter - Structured event logging for Agent Runs.

Each Agent Run outputs trace.jsonl with one JSON object per line.
Events follow the schema in Section 4.4 of the spec.

No API keys, environment variables, or secrets are written to trace.
"""

import json
import time
from dataclasses import dataclass, field


@dataclass
class TraceEvent:
    """A single trace event."""
    run_id: str
    event_id: str
    event_type: str
    timestamp: float
    step: int
    issue_id: str | None
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "step": self.step,
            "issue_id": self.issue_id,
            "payload": self.payload,
        }


class TraceWriter:
    """Writes structured trace events to a JSONL file.

    Events are buffered in memory until save() is called.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._events: list[TraceEvent] = []
        self._event_counter = 0

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"evt_{self.run_id}_{self._event_counter:04d}"

    def add(self, event_type: str, step: int = 0,
            issue_id: str | None = None, payload: dict | None = None):
        """Add a trace event."""
        if payload is None:
            payload = {}
        event = TraceEvent(
            run_id=self.run_id,
            event_id=self._next_event_id(),
            event_type=event_type,
            timestamp=time.time(),
            step=step,
            issue_id=issue_id,
            payload=payload,
        )
        self._events.append(event)

    def run_started(self, project_root: str, platform: str, config: dict | None = None):
        """Record run_started event."""
        payload = {"project_root": project_root, "platform": platform}
        if config:
            payload["agent_config"] = {
                k: v for k, v in config.items()
                if k not in ("api_key", "secret", "token", "password")
            }
        self.add("run_started", step=0, payload=payload)

    def model_requested(self, step: int, issue_id: str, model: str,
                        messages_count: int, tool_count: int):
        """Record model_requested event (no full prompt content)."""
        self.add("model_requested", step=step, issue_id=issue_id, payload={
            "model": model,
            "messages_count": messages_count,
            "available_tools": tool_count,
        })

    def model_responded(self, step: int, issue_id: str, action: str,
                        tool_name: str | None = None):
        """Record model_responded event."""
        payload = {"action": action}
        if tool_name:
            payload["tool_name"] = tool_name
        self.add("model_responded", step=step, issue_id=issue_id, payload=payload)

    def tool_requested(self, step: int, issue_id: str, tool_name: str,
                       arguments: dict):
        """Record tool_requested event."""
        self.add("tool_requested", step=step, issue_id=issue_id, payload={
            "tool_name": tool_name,
            "arguments": arguments,
        })

    def tool_completed(self, step: int, issue_id: str, tool_name: str,
                       tool_result_id: str, ok: bool):
        """Record tool_completed event."""
        self.add("tool_completed", step=step, issue_id=issue_id, payload={
            "tool_name": tool_name,
            "tool_result_id": tool_result_id,
            "ok": ok,
        })

    def tool_failed(self, step: int, issue_id: str, tool_name: str,
                    error_code: str, message: str):
        """Record tool_failed event."""
        self.add("tool_failed", step=step, issue_id=issue_id, payload={
            "tool_name": tool_name,
            "error_code": error_code,
            "message": message,
        })

    def guardrail_triggered(self, step: int, issue_id: str, reason: str):
        """Record guardrail_triggered event."""
        self.add("guardrail_triggered", step=step, issue_id=issue_id, payload={
            "reason": reason,
        })

    def checkpoint_saved(self, step: int):
        """Record checkpoint_saved event."""
        self.add("checkpoint_saved", step=step)

    def fallback_used(self, reason: str):
        """Record fallback_used event."""
        self.add("fallback_used", step=0, payload={"reason": reason})

    def worker_started(self, worker_id: int, group_key: str):
        """Record worker_started event (parallel mode)."""
        self.add("worker_started", step=0, payload={
            "worker_id": worker_id,
            "group_key": group_key,
        })

    def worker_completed(self, worker_id: int, group_key: str,
                         assessments: int = 0, tool_calls: int = 0,
                         success: bool = True):
        """Record worker_completed event (parallel mode)."""
        self.add("worker_completed", step=0, payload={
            "worker_id": worker_id,
            "group_key": group_key,
            "assessments_produced": assessments,
            "tool_calls": tool_calls,
            "success": success,
        })

    def run_completed(self, step: int, status: str,
                      assessments_count: int = 0,
                      tool_calls: int = 0):
        """Record run_completed event."""
        self.add("run_completed", step=step, payload={
            "status": status,
            "assessments_count": assessments_count,
            "tool_calls": tool_calls,
        })

    def save(self, path: str):
        """Write all events to a JSONL file."""
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for event in self._events:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)
