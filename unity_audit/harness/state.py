"""RunState and checkpoint management.

RunState is JSON-serializable and tracks all agent progress.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class RunStatus(str, Enum):
    """Allowed terminal states for a Run."""
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FALLBACK = "completed_with_fallback"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    FAILED = "failed"


@dataclass
class ToolCallRecord:
    """Record of a single tool call."""
    tool_result_id: str
    tool_name: str
    arguments: dict
    result: dict  # ToolResult.to_dict()
    timestamp: float = field(default_factory=time.time)
    step: int = 0


@dataclass
class AgentAssessment:
    """A structured assessment from the agent."""
    issue_id: str
    risk_level: str  # low, medium, high
    recommended_action: str  # auto_fix_candidate, manual_confirm_required, do_not_fix
    confidence: float  # 0.0 - 1.0
    summary: str
    evidence_refs: list[str] = field(default_factory=list)
    needs_human_review: bool = False


@dataclass
class RunState:
    """Full state of an Agent Run, JSON serializable."""
    run_id: str
    status: str = "running"  # RunStatus value
    project_root: str = ""
    platform: str = "Unknown"
    current_issue_id: str | None = None
    pending_issue_ids: list[str] = field(default_factory=list)
    completed_issue_ids: list[str] = field(default_factory=list)
    step_count: int = 0
    max_steps: int = 12
    tool_results: list[ToolCallRecord] = field(default_factory=list)
    agent_assessments: list[AgentAssessment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @staticmethod
    def generate_run_id() -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    def add_tool_result(self, record: ToolCallRecord):
        self.tool_results.append(record)
        self.touch()

    def add_assessment(self, assessment: AgentAssessment):
        self.agent_assessments.append(assessment)
        self.touch()

    def add_error(self, error: str):
        self.errors.append(error)
        self.touch()

    def complete_issue(self, issue_id: str):
        if issue_id in self.pending_issue_ids:
            self.pending_issue_ids.remove(issue_id)
        if issue_id not in self.completed_issue_ids:
            self.completed_issue_ids.append(issue_id)
        self.touch()

    def pop_next_issue(self) -> str | None:
        if self.pending_issue_ids:
            next_id = self.pending_issue_ids[0]
            self.current_issue_id = next_id
            self.touch()
            return next_id
        return None

    def increment_step(self):
        self.step_count += 1
        self.touch()

    def is_terminal(self) -> bool:
        return self.status in (
            RunStatus.COMPLETED.value,
            RunStatus.COMPLETED_WITH_FALLBACK.value,
            RunStatus.WAITING_FOR_APPROVAL.value,
            RunStatus.FAILED.value,
        )

    def touch(self):
        self.updated_at = time.time()

    def merge(self, other: "RunState"):
        """Merge another RunState into this one (thread-safe with external lock).

        Combines tool_results, agent_assessments, errors, and
        completed_issue_ids. The caller is responsible for holding a lock
        if called from multiple threads.

        Args:
            other: Another RunState to merge into this one. Its lists are
                   appended to this state's lists.
        """
        self.tool_results.extend(other.tool_results)
        self.agent_assessments.extend(other.agent_assessments)
        self.errors.extend(other.errors)
        for iid in other.completed_issue_ids:
            if iid not in self.completed_issue_ids:
                self.completed_issue_ids.append(iid)
        self.step_count += other.step_count
        self.touch()

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "project_root": self.project_root,
            "platform": self.platform,
            "current_issue_id": self.current_issue_id,
            "pending_issue_ids": self.pending_issue_ids,
            "completed_issue_ids": self.completed_issue_ids,
            "step_count": self.step_count,
            "max_steps": self.max_steps,
            "tool_results": [
                {
                    "tool_result_id": r.tool_result_id,
                    "tool_name": r.tool_name,
                    "arguments": r.arguments,
                    "result": r.result,
                    "timestamp": r.timestamp,
                    "step": r.step,
                }
                for r in self.tool_results
            ],
            "agent_assessments": [
                {
                    "issue_id": a.issue_id,
                    "risk_level": a.risk_level,
                    "recommended_action": a.recommended_action,
                    "confidence": a.confidence,
                    "summary": a.summary,
                    "evidence_refs": a.evidence_refs,
                    "needs_human_review": a.needs_human_review,
                }
                for a in self.agent_assessments
            ],
            "errors": self.errors,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunState":
        state = cls(
            run_id=data["run_id"],
            status=data.get("status", "running"),
            project_root=data.get("project_root", ""),
            platform=data.get("platform", "Unknown"),
            current_issue_id=data.get("current_issue_id"),
            pending_issue_ids=data.get("pending_issue_ids", []),
            completed_issue_ids=data.get("completed_issue_ids", []),
            step_count=data.get("step_count", 0),
            max_steps=data.get("max_steps", 12),
            errors=data.get("errors", []),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
        for r in data.get("tool_results", []):
            state.tool_results.append(ToolCallRecord(
                tool_result_id=r["tool_result_id"],
                tool_name=r["tool_name"],
                arguments=r["arguments"],
                result=r["result"],
                timestamp=r.get("timestamp", 0),
                step=r.get("step", 0),
            ))
        for a in data.get("agent_assessments", []):
            state.agent_assessments.append(AgentAssessment(
                issue_id=a["issue_id"],
                risk_level=a["risk_level"],
                recommended_action=a["recommended_action"],
                confidence=a["confidence"],
                summary=a["summary"],
                evidence_refs=a.get("evidence_refs", []),
                needs_human_review=a.get("needs_human_review", False),
            ))
        return state

    def save(self, path: str):
        """Save RunState to a JSON file (checkpoint)."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "RunState":
        """Load RunState from a JSON file (resume from checkpoint)."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def save_assessments(self, path: str):
        """Save agent assessments to a standalone JSON file."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                [a.__dict__ if hasattr(a, '__dict__') else {
                    "issue_id": a.issue_id,
                    "risk_level": a.risk_level,
                    "recommended_action": a.recommended_action,
                    "confidence": a.confidence,
                    "summary": a.summary,
                    "evidence_refs": a.evidence_refs,
                    "needs_human_review": a.needs_human_review,
                } for a in self.agent_assessments],
                f, ensure_ascii=False, indent=2,
            )
