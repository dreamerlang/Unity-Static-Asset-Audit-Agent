"""Approval protocol - Stub for future write-tool approval.

This version (v0.2) does NOT register any write tools, so approvals
are never actually needed. This module exists as a protocol stub for
future versions that may add auto-fix capabilities.
"""

from dataclasses import dataclass
from enum import Enum


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ApprovalRequest:
    """A request for human approval before a write operation."""
    request_id: str
    tool_name: str
    description: str
    affected_files: list[str]
    risk_level: str

    # These are filled after human review
    status: str = ApprovalStatus.PENDING.value
    reviewer_note: str | None = None


class ApprovalManager:
    """Manages approval requests for write operations.

    In v0.2, this is a stub. No write tools are registered, so no approvals
    are needed. All writes would be rejected by default.
    """

    def __init__(self):
        self._requests: dict[str, ApprovalRequest] = {}

    def request_approval(self, tool_name: str, description: str,
                         affected_files: list[str],
                         risk_level: str = "medium") -> ApprovalRequest:
        """Create an approval request. In v0.2, auto-rejects all writes."""
        import uuid
        req = ApprovalRequest(
            request_id=f"apr_{uuid.uuid4().hex[:8]}",
            tool_name=tool_name,
            description=description,
            affected_files=affected_files,
            risk_level=risk_level,
            status=ApprovalStatus.REJECTED.value,
            reviewer_note="Write tools are not available in v0.2",
        )
        self._requests[req.request_id] = req
        return req

    def is_approved(self, request_id: str) -> bool:
        req = self._requests.get(request_id)
        return req is not None and req.status == ApprovalStatus.APPROVED.value
