"""Domain models for the Audit Service."""
import time
from dataclasses import dataclass, field

from unity_audit.evidence import EvidenceResult
from unity_audit.fix_planner import FixDecision
from unity_audit.meta_parser import MetaInfo
from unity_audit.rules.engine import Issue
from unity_audit.scanner import AssetInfo


@dataclass
class AuditRequest:
    """Input request for an audit scan."""
    project_root: str
    platform: str = "Unknown"
    config: dict | None = None
    path_filter: set[str] | None = None  # For incremental scans


@dataclass
class AuditResult:
    """Complete result of a deterministic audit scan."""
    project_root: str
    platform: str
    assets: list[AssetInfo] = field(default_factory=list)
    meta_map: dict[str, MetaInfo] = field(default_factory=dict)
    extracted_map: dict[str, object] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    evidence_map: dict[str, EvidenceResult] = field(default_factory=dict)
    fix_decisions: list[FixDecision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def elapsed_seconds(self) -> float:
        if self.finished_at is not None:
            return self.finished_at - self.started_at
        return time.time() - self.started_at
