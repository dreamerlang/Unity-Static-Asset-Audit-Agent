"""GroupCoordinator - Thread-pool dispatcher for parallel issue group processing.

Manages a ThreadPoolExecutor to fan out issue groups to GroupWorker instances.
Each worker runs independently; results are collected and merged into the
master RunState.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from unity_audit.application.models import AuditResult
from unity_audit.harness.assessment_guardrails import retarget_fix_plan
from unity_audit.harness.state import (
    AgentAssessment,
    RunState,
    RunStatus,
)
from unity_audit.harness.tools import ToolRegistry
from unity_audit.harness.tracing import TraceWriter
from unity_audit.harness.worker import GroupWorker, WorkerResult


class GroupCoordinator:
    """Dispatches issue groups to parallel workers and merges results.

    Usage:
        coordinator = GroupCoordinator(
            agent_factory=lambda: AuditAgent(create_model_client(...)),
            tool_registry=registry,
            audit_result=audit_result,
            max_workers=5,
            max_steps_per_group=20,
            trace_enabled=True,
        )
        final_state = coordinator.dispatch(
            groups=groups,       # list of (group_key, rep_data, peer_ids)
            master_state=state,  # initial RunState
            trace_writer=writer, # shared TraceWriter
        )
    """

    def __init__(
        self,
        agent_factory,  # Callable[[], AuditAgent]
        tool_registry: ToolRegistry,
        audit_result: AuditResult,
        max_workers: int = 5,
        max_steps_per_group: int = 20,
        max_total_steps: int | None = None,
        trace_enabled: bool = True,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")

        self.agent_factory = agent_factory
        self.tool_registry = tool_registry
        self.audit_result = audit_result
        self.max_workers = max_workers
        self.max_steps_per_group = max_steps_per_group
        self.max_total_steps = max_total_steps
        self.trace_enabled = trace_enabled

        # Lock for merging results and writing trace
        self._merge_lock = threading.Lock()

    def dispatch(
        self,
        groups: list[tuple],  # list of (group_key, rep_data, peer_ids)
        master_state: RunState,
        trace_writer: TraceWriter | None = None,
    ) -> RunState:
        """Dispatch groups to workers and merge results.

        Args:
            groups: List of (group_key, representative_data, peer_ids) tuples.
                    group_key: (rule_id, severity, path_category) tuple.
                    representative_data: Issue data dict for the representative.
                    peer_ids: List of peer issue IDs for broadcast.
            master_state: Initial RunState to merge results into.
            trace_writer: Optional shared TraceWriter. Thread-safe writing
                          is ensured via the merge lock.

        Returns:
            Updated master_state with all worker results merged.
        """
        if not groups:
            master_state.status = RunStatus.COMPLETED.value
            return master_state

        # Single worker — run sequentially without thread overhead
        if self.max_workers == 1:
            return self._dispatch_sequential(groups, master_state, trace_writer)

        return self._dispatch_parallel(groups, master_state, trace_writer)

    def _dispatch_sequential(
        self,
        groups: list[tuple],
        master_state: RunState,
        trace_writer: TraceWriter | None = None,
    ) -> RunState:
        """Run groups one at a time (max_workers=1, backward-compatible path)."""
        agent = self.agent_factory()
        worker = GroupWorker(
            agent=agent,
            tool_registry=self.tool_registry,
            audit_result=self.audit_result,
            max_steps=self.max_steps_per_group,
            trace_enabled=self.trace_enabled,
            worker_id=0,
        )

        for group_key, rep_data, peer_ids in groups:
            if master_state.is_terminal():
                break

            result = worker.run(group_key, rep_data, peer_ids, trace_writer)
            self._merge_result(master_state, result, trace_writer)

        expected = {rep_data["issue_id"] for _, rep_data, _ in groups}
        assessed = {assessment.issue_id for assessment in master_state.agent_assessments}
        master_state.status = (
            RunStatus.COMPLETED.value
            if expected.issubset(assessed)
            else RunStatus.COMPLETED_WITH_FALLBACK.value
        )

        return master_state

    def _dispatch_parallel(
        self,
        groups: list[tuple],
        master_state: RunState,
        trace_writer: TraceWriter | None = None,
    ) -> RunState:
        """Run groups in parallel using ThreadPoolExecutor."""
        # Limit workers to number of groups
        actual_workers = min(self.max_workers, len(groups))

        if self.max_total_steps is None:
            budgets = [self.max_steps_per_group] * len(groups)
        else:
            base, remainder = divmod(self.max_total_steps, len(groups))
            budgets = [base + (1 if index < remainder else 0)
                       for index in range(len(groups))]

        results: list[WorkerResult] = []
        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            # Submit all groups
            futures = {}
            for i, (group_key, rep_data, peer_ids) in enumerate(groups):
                # Each worker gets its own AuditAgent instance
                agent = self.agent_factory()
                if budgets[i] == 0:
                    results.append(WorkerResult(
                        group_key=group_key,
                        representative_id=rep_data["issue_id"],
                        peer_ids=list(peer_ids),
                        fallback=True,
                    ))
                    continue
                worker = GroupWorker(
                    agent=agent,
                    tool_registry=self.tool_registry,
                    audit_result=self.audit_result,
                    max_steps=min(self.max_steps_per_group, budgets[i]),
                    trace_enabled=self.trace_enabled,
                    worker_id=i,
                )
                future = executor.submit(
                    worker.run, group_key, rep_data, peer_ids, trace_writer
                )
                futures[future] = i

            # Collect results as they complete
            completed_count = 0
            for future in as_completed(futures):
                worker_idx = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    # Worker crashed — record as error
                    group_key, rep_data, peer_ids = groups[worker_idx]
                    result = WorkerResult(
                        group_key=group_key,
                        representative_id=rep_data["issue_id"] if rep_data else "unknown",
                        peer_ids=list(peer_ids),
                        errors=[f"Worker {worker_idx} crashed: {e}"],
                        fallback=True,
                    )

                self._merge_result(master_state, result, trace_writer)
                results.append(result)
                completed_count += 1

        for result in results:
            if result.step_count == 0 and result.fallback:
                self._merge_result(master_state, result, trace_writer)

        # Determine final status
        master_state.status = (
            RunStatus.COMPLETED.value
            if results and all(result.success for result in results)
            else RunStatus.COMPLETED_WITH_FALLBACK.value
        )

        return master_state

    def _merge_result(
        self,
        master_state: RunState,
        result: WorkerResult,
        trace_writer: TraceWriter | None = None,
    ):
        """Merge a single WorkerResult into the master RunState.

        Handles assessment broadcasting to peer issues and tool result
        accumulation. Thread-safe via self._merge_lock.

        Args:
            master_state: The master RunState to merge into.
            result: WorkerResult from a completed GroupWorker.
            trace_writer: Optional TraceWriter.
        """
        with self._merge_lock:
            # Merge tool results and errors
            master_state.tool_results.extend(result.tool_results)
            master_state.errors.extend(result.errors)

            # Merge step count
            master_state.step_count += result.step_count

            # Add assessment for representative
            if result.assessment is not None:
                # Always use the actual representative_id (not the model's response)
                # to prevent hallucinated issue_ids from corrupting state.
                assessment = result.assessment
                if assessment.issue_id != result.representative_id:
                    assessment = AgentAssessment(
                        issue_id=result.representative_id,
                        risk_level=assessment.risk_level,
                        recommended_action=assessment.recommended_action,
                        confidence=assessment.confidence,
                        summary=assessment.summary,
                        evidence_refs=list(assessment.evidence_refs),
                        needs_human_review=assessment.needs_human_review,
                        usage_context=assessment.usage_context,
                        evidence_strength=assessment.evidence_strength,
                        fix_plan=assessment.fix_plan,
                    )
                master_state.add_assessment(assessment)
                master_state.complete_issue(result.representative_id)

                # Broadcast to peers
                for peer_id in result.peer_ids:
                    peer_data = self._get_issue_data(peer_id)
                    peer_asset = peer_data["asset_path"] if peer_data else peer_id
                    peer_summary = (
                        f"[同组评估，基于 {result.representative_id} 的分析] "
                        f"{peer_asset}: {assessment.summary[:180]}"
                    )
                    peer_assessment = AgentAssessment(
                        issue_id=peer_id,
                        risk_level=assessment.risk_level,
                        recommended_action=assessment.recommended_action,
                        confidence=assessment.confidence,
                        summary=peer_summary,
                        evidence_refs=list(assessment.evidence_refs),
                        needs_human_review=assessment.needs_human_review,
                        usage_context=assessment.usage_context,
                        evidence_strength=assessment.evidence_strength,
                        fix_plan=retarget_fix_plan(assessment.fix_plan, peer_asset),
                    )
                    master_state.add_assessment(peer_assessment)
                    master_state.complete_issue(peer_id)

                # Trace broadcast
                if trace_writer and self.trace_enabled:
                    trace_writer.model_responded(
                        master_state.step_count,
                        assessment.issue_id,
                        "broadcast",
                        tool_name=None,
                    )
            else:
                # Representative failed — mark all as fallback
                master_state.complete_issue(result.representative_id)
                for peer_id in result.peer_ids:
                    master_state.complete_issue(peer_id)

    def _get_issue_data(self, issue_id: str) -> dict | None:
        """Get issue data from audit result."""
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
                    "path_category": GroupWorker._classify_path(issue.asset_path),
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
