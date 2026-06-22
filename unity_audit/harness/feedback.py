"""Project-local human feedback retrieval for Agent assessments."""

import fnmatch
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


VALID_FEEDBACK_DECISIONS = {
    "accepted_fix", "rejected_fix", "false_positive", "manual_exception",
}


@dataclass
class FeedbackRecord:
    """A human decision from a previous audit review."""

    rule_id: str
    asset_path_pattern: str
    decision: str
    reason: str
    feedback_id: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def default_feedback_path(project_root: str) -> str:
    """Return the conventional project-local feedback file path."""
    return os.path.join(project_root, ".unity-audit", "feedback.jsonl")


def load_project_feedback(
    project_root: str,
    max_records: int = 1000,
) -> tuple[list[FeedbackRecord], list[str]]:
    """Load valid feedback records without failing the audit on malformed lines."""
    path = default_feedback_path(project_root)
    if not os.path.isfile(path):
        return [], []

    records = []
    warnings = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line_number, line in enumerate(f, start=1):
                if len(records) >= max_records:
                    warnings.append(f"Feedback truncated at {max_records} records")
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError as e:
                    warnings.append(f"Invalid feedback JSON at line {line_number}: {e.msg}")
                    continue
                required = ("rule_id", "asset_path_pattern", "decision", "reason")
                if not isinstance(data, dict) or any(
                    not isinstance(data.get(key), str) or not data.get(key)
                    for key in required
                ):
                    warnings.append(f"Invalid feedback record at line {line_number}")
                    continue
                records.append(FeedbackRecord(
                    rule_id=data["rule_id"],
                    asset_path_pattern=data["asset_path_pattern"],
                    decision=data["decision"],
                    reason=data["reason"],
                    feedback_id=str(data.get("feedback_id", "")),
                    created_at=str(data.get("created_at", "")),
                ))
    except OSError as e:
        warnings.append(f"Could not read feedback file: {e}")

    return records, warnings


def find_relevant_feedback(
    records: list[FeedbackRecord],
    rule_id: str,
    asset_path: str,
    limit: int = 5,
) -> list[dict]:
    """Return feedback matching both rule and asset path, most specific first."""
    matches = []
    normalized_path = asset_path.replace("\\", "/")
    for record in records:
        if record.rule_id not in {rule_id, "*"}:
            continue
        pattern = record.asset_path_pattern.replace("\\", "/")
        if not fnmatch.fnmatchcase(normalized_path, pattern):
            continue
        literal_chars = len(pattern.replace("*", "").replace("?", ""))
        rule_score = 2 if record.rule_id == rule_id else 0
        exact_score = 3 if pattern == normalized_path else 0
        matches.append((rule_score + exact_score, literal_chars, record))

    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [record.to_dict() for _, _, record in matches[:limit]]


def append_project_feedback(
    project_root: str,
    rule_id: str,
    asset_path_pattern: str,
    decision: str,
    reason: str,
) -> FeedbackRecord:
    """Append a validated human feedback record to the project-local store."""
    if decision not in VALID_FEEDBACK_DECISIONS:
        raise ValueError(f"Invalid feedback decision: {decision}")
    if not rule_id or not asset_path_pattern or not reason:
        raise ValueError("rule_id, asset_path_pattern, and reason are required")

    record = FeedbackRecord(
        feedback_id=f"fb_{uuid.uuid4().hex[:12]}",
        rule_id=rule_id,
        asset_path_pattern=asset_path_pattern.replace("\\", "/"),
        decision=decision,
        reason=reason,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    path = default_feedback_path(project_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    return record
