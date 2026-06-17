"""GroupWorker - Encapsulates single-group agent processing for parallel execution.

Each GroupWorker runs one issue group (representative + peers) independently
in a thread. It creates its own AuditAgent, manages its own tool results,
and returns a WorkerResult bundle for the coordinator to merge.
"""

import uuid
from dataclasses import dataclass, field

from unity_audit.application.models import AuditResult
from unity_audit.harness.policy import validate_action
from unity_audit.harness.state import (
    AgentAssessment,
    RunState,
    RunStatus,
    ToolCallRecord,
)
from unity_audit.harness.tools import ToolRegistry
from unity_audit.harness.tracing import TraceWriter


@dataclass
class WorkerResult:
    """Result bundle returned by a GroupWorker after processing one group.

    Collected by GroupCoordinator and merged into the master RunState.
    """
    group_key: tuple  # (rule_id, severity, path_category)
    representative_id: str
    peer_ids: list[str] = field(default_factory=list)
    assessment: AgentAssessment | None = None
    tool_results: list[ToolCallRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    step_count: int = 0
    fallback: bool = False

    @property
    def success(self) -> bool:
        return self.assessment is not None and not self.fallback


class GroupWorker:
    """Processes a single issue group through the agent loop.

    Designed to run in a ThreadPoolExecutor. Each worker creates its own
    AuditAgent and ToolRegistry instances to avoid thread-safety issues.

    Usage:
        worker = GroupWorker(
            agent=audit_agent,           # AuditAgent with its own ModelClient
            tool_registry=tool_registry, # ToolRegistry (read-only, safe to share)
            audit_result=audit_result,
            max_steps=20,
            trace_enabled=False,
            worker_id=0,
        )
        result = worker.run(
            group_key=("TEX_RW", "high", "ui"),
            representative_data={...},  # issue_data dict for the rep
            peer_ids=["TEX_RW_1", "TEX_RW_2"],
            trace_writer=None,  # Optional shared TraceWriter with lock
        )
    """

    def __init__(
        self,
        agent,  # AuditAgent instance
        tool_registry: ToolRegistry,
        audit_result: AuditResult,
        max_steps: int = 20,
        trace_enabled: bool = False,
        worker_id: int = 0,
    ):
        self.agent = agent
        self.tool_registry = tool_registry
        self.audit_result = audit_result
        self.max_steps = max_steps
        self.trace_enabled = trace_enabled
        self.worker_id = worker_id
        self._retry_counts: dict[tuple[str, str, int], int] = {}

    def _hash_arguments(self, args: dict) -> int:
        """Hash tool arguments for deduplication (copied from HarnessRunner)."""

        def _make_hashable(obj):
            if isinstance(obj, dict):
                return tuple(sorted((k, _make_hashable(v)) for k, v in obj.items()))
            elif isinstance(obj, list):
                return tuple(_make_hashable(i) for i in obj)
            return obj

        try:
            return hash(tuple(sorted(
                (k, _make_hashable(v)) for k, v in args.items()
            )))
        except Exception:
            import json
            return hash(json.dumps(args, sort_keys=True))

    def _get_issue_data(self, issue_id: str) -> dict | None:
        """Get issue data from the audit result, enriched with fix decision."""
        for issue in self.audit_result.issues:
            if issue.issue_id == issue_id:
                data = {
                    "issue_id": issue.issue_id,
                    "rule_id": issue.rule_id,
                    "severity": issue.severity,
                    "asset_path": issue.asset_path,
                    "title": issue.title,
                    "message": issue.message,
                    "evidence": issue.evidence,
                    "suggestion": issue.suggestion,
                    "auto_fixable": issue.auto_fixable,
                    "path_category": self._classify_path(issue.asset_path),
                }
                decision = None
                for d in self.audit_result.fix_decisions:
                    if d.issue_id == issue_id:
                        decision = d
                        break
                if decision is not None:
                    data["deterministic_fix_decision"] = {
                        "action": decision.action,
                        "risk_level": decision.risk_level,
                        "reason": decision.reason,
                        "suggestion": decision.suggestion,
                    }
                return data
        return None

    @staticmethod
    def _classify_path(asset_path: str) -> str:
        """Classify an asset path into a broad category for grouping."""
        import re
        path = asset_path.replace('\\', '/')
        classifiers = [
            (r'(^|/)ReferenceImages/', 'reference_images'),
            (r'(^|/)Snapshots/', 'reference_images'),
            (r'(^|/)Editor/', 'editor_only'),
            (r'(^|/)Editor Only/', 'editor_only'),
            (r'(^|/)ThirdParty/', 'third_party'),
            (r'(^|/)Plugins/', 'third_party'),
            (r'(^|/)UI/', 'ui'),
            (r'(^|/)Scenes/UI/', 'ui'),
            (r'(^|/)Characters/', 'character'),
            (r'(^|/)Models/', 'character'),
            (r'(^|/)Audio/SFX/', 'audio'),
            (r'(^|/)Audio/Music/', 'audio'),
            (r'(^|/)Audio/', 'audio'),
            (r'(^|/)Scenes/', 'scene'),
            (r'(^|/)Prefabs/', 'prefab'),
            (r'(^|/)Resources/', 'resource'),
            (r'(^|/)Textures/', 'texture'),
        ]
        for pattern, category in classifiers:
            if re.search(pattern, path):
                return category
        return 'unknown'

    def run(
        self,
        group_key: tuple,
        representative_data: dict,
        peer_ids: list[str],
        trace_writer: TraceWriter | None = None,
    ) -> WorkerResult:
        """Process one issue group through the agent loop.

        Args:
            group_key: (rule_id, severity, path_category) tuple.
            representative_data: Issue data dict for the representative.
            peer_ids: Issue IDs that will receive cloned assessments.
            trace_writer: Optional shared TraceWriter. Caller must hold a lock
                          if sharing across threads.

        Returns:
            WorkerResult with assessment, tool_results, and errors.
        """
        result = WorkerResult(
            group_key=group_key,
            representative_id=representative_data["issue_id"],
            peer_ids=list(peer_ids),
        )

        # Trace worker start
        if trace_writer and self.trace_enabled:
            trace_writer.worker_started(
                self.worker_id,
                f"{group_key[0]}/{group_key[1]}/{group_key[2]}",
            )

        try:
            assessment, tool_results, step_count = self._process_representative(
                representative_data, trace_writer,
            )
            result.tool_results = tool_results
            result.step_count = step_count
            if assessment is not None:
                result.assessment = assessment
            else:
                result.fallback = True
        except Exception as e:
            result.errors.append(
                f"Worker {self.worker_id} error for {result.representative_id}: {e}"
            )
            result.fallback = True

        # Trace worker complete
        if trace_writer and self.trace_enabled:
            trace_writer.worker_completed(
                self.worker_id,
                f"{group_key[0]}/{group_key[1]}/{group_key[2]}",
                assessments=1 if result.assessment else 0,
                tool_calls=len(result.tool_results),
                success=result.success,
            )

        return result

    def _process_representative(
        self,
        issue_data: dict,
        trace_writer: TraceWriter | None = None,
    ) -> tuple[AgentAssessment | None, list[ToolCallRecord], int]:
        """Process a representative issue through the agent loop.

        Returns:
            Tuple of (assessment_or_none, tool_results, step_count).
        """
        return self._process_issue_inner(issue_data, trace_writer)

    def _process_issue_inner(
        self,
        issue_data: dict,
        trace_writer: TraceWriter | None = None,
    ) -> tuple[AgentAssessment | None, list[ToolCallRecord], int]:
        """Core issue processing loop — self-contained version for workers.

        Args:
            issue_data: Issue context dict.
            trace_writer: Optional shared TraceWriter.

        Returns:
            Tuple of (AgentAssessment or None, tool_results, step_count).
        """
        issue_id = issue_data["issue_id"]
        registry_tool_names = {t.name for t in self.tool_registry.list_tools()}
        existing_result_ids: set[str] = set()
        local_tool_results: list[ToolCallRecord] = []
        results_before = 0  # Track per-issue progress in local list
        step_count = 0

        while step_count < self.max_steps:
            step_count += 1
            remaining = self.max_steps - step_count

            # Dynamic tool list: prevent redundant calls and force submission
            all_tool_defs = self.tool_registry.list_tools()

            # Track which info-gathering tools have succeeded for THIS issue
            successful_tools = set()
            for tr in local_tool_results[results_before:]:
                if tr.result.get("ok"):
                    successful_tools.add(tr.tool_name)

            # Progressive tool restriction
            info_tools_used = successful_tools & {
                "get_issue_detail", "get_asset_info", "search_asset_code",
                "list_project_assets", "read_code_context",
                "trace_prefab_references",
            }
            if remaining <= 2:
                tool_defs = [t for t in all_tool_defs if t.name == "submit_assessment"]
            elif len(info_tools_used) >= 3:
                tool_defs = [t for t in all_tool_defs if t.name == "submit_assessment"]
            elif "get_issue_detail" in successful_tools:
                tool_defs = [t for t in all_tool_defs
                             if t.name in ("submit_assessment", "search_asset_code",
                                           "get_asset_info", "read_code_context",
                                           "trace_prefab_references")]
            else:
                tool_defs = all_tool_defs

            try:
                # Trace model request
                if trace_writer and self.trace_enabled:
                    trace_writer.model_requested(
                        step_count, issue_id, self.agent.model_name,
                        messages_count=1,
                        tool_count=len(tool_defs),
                    )

                action = self.agent.get_action(
                    issue_data=issue_data,
                    tool_defs=tool_defs,
                    tool_results=local_tool_results,
                    step=step_count,
                    max_steps=self.max_steps,
                    rule_id=issue_data.get("rule_id"),
                )
            except Exception as e:
                if trace_writer and self.trace_enabled:
                    trace_writer.guardrail_triggered(
                        step_count, issue_id, f"Model error: {e}"
                    )
                return None, local_tool_results, step_count  # Fallback

            # Validate action structure
            valid, error_msg = validate_action(
                action, registry_tool_names, existing_result_ids,
                original_issue=issue_data,
            )
            if not valid:
                if trace_writer and self.trace_enabled:
                    trace_writer.guardrail_triggered(
                        step_count, issue_id, f"Validation: {error_msg}"
                    )
                return None, local_tool_results, step_count

            # Trace the model response
            action_type = action["action"]
            if trace_writer and self.trace_enabled:
                trace_writer.model_responded(
                    step_count, issue_id, action_type,
                    tool_name=action.get("tool_name"),
                )

            # Execute tool or finish
            if action_type == "finish":
                assessment_dict = action["assessment"]
                return AgentAssessment(
                    issue_id=assessment_dict["issue_id"],
                    risk_level=assessment_dict["risk_level"],
                    recommended_action=assessment_dict["recommended_action"],
                    confidence=assessment_dict["confidence"],
                    summary=assessment_dict["summary"],
                    evidence_refs=assessment_dict.get("evidence_refs", []),
                    needs_human_review=assessment_dict.get("needs_human_review", False),
                ), local_tool_results, step_count

            elif action_type == "call_tool":
                tool_name = action["tool_name"]
                arguments = action["arguments"]

                # Check retry limit
                args_hash = self._hash_arguments(arguments)
                retry_key = (issue_id, tool_name, args_hash)
                retry_count = self._retry_counts.get(retry_key, 0)
                if retry_count > 1:
                    if trace_writer and self.trace_enabled:
                        trace_writer.guardrail_triggered(
                            step_count, issue_id,
                            f"Retry limit exceeded for {tool_name}"
                        )
                    continue

                # Execute tool
                if trace_writer and self.trace_enabled:
                    trace_writer.tool_requested(
                        step_count, issue_id, tool_name, arguments
                    )

                tool_result = self.tool_registry.execute(tool_name, arguments)

                # Generate tool result ID
                tool_result.tool_result_id = f"tr_{uuid.uuid4().hex[:8]}"

                # Record result
                record = ToolCallRecord(
                    tool_result_id=tool_result.tool_result_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=tool_result.to_dict(),
                    step=step_count,
                )
                local_tool_results.append(record)
                existing_result_ids.add(tool_result.tool_result_id)

                # Check for submit_assessment — this completes the issue
                if tool_name == "submit_assessment" and tool_result.ok:
                    if trace_writer and self.trace_enabled:
                        trace_writer.tool_completed(
                            step_count, issue_id, tool_name,
                            tool_result.tool_result_id, True,
                        )
                    return AgentAssessment(
                        issue_id=arguments.get("issue_id", issue_id),
                        risk_level=arguments.get("risk_level", "medium"),
                        recommended_action=arguments.get("recommended_action", "manual_confirm_required"),
                        confidence=arguments.get("confidence", 0.5),
                        summary=arguments.get("summary", ""),
                        evidence_refs=arguments.get("evidence_refs", []),
                        needs_human_review=arguments.get("needs_human_review", False),
                    ), local_tool_results, step_count

                if tool_result.ok:
                    if trace_writer and self.trace_enabled:
                        trace_writer.tool_completed(
                            step_count, issue_id, tool_name,
                            tool_result.tool_result_id, True,
                        )
                    self._retry_counts.pop(retry_key, None)
                else:
                    if trace_writer and self.trace_enabled:
                        trace_writer.tool_failed(
                            step_count, issue_id, tool_name,
                            tool_result.error_code or "TOOL_ERROR",
                            tool_result.message,
                        )
                    self._retry_counts[retry_key] = retry_count + 1

        # If we exit the loop without finishing, fallback
        return None, local_tool_results, step_count
