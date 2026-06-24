"""HarnessRunner - Controlled execution loop for the Agent.

Implements the step-by-step loop:
  read RunState -> get model action -> validate -> execute tool/finish
  -> write ToolResult/Trace/Checkpoint -> check termination

Constraints:
- max_steps (default 12)
- One tool per step
- No parallel tool calls (within a single agent)
- Max 1 retry for same tool+params failure
- Fallback to deterministic on any failure

Multi-Agent (v0.1):
- When max_workers > 1, uses GroupCoordinator + ThreadPoolExecutor to
  process issue groups in parallel.
- Each worker gets its own AuditAgent (via agent_factory) for thread safety.
"""

import uuid

from unity_audit.application.models import AuditResult
from unity_audit.harness.assessment_guardrails import (
    retarget_fix_plan,
    validate_assessment_guardrails,
)
from unity_audit.harness.policy import validate_action, validate_assessment_payload
from unity_audit.harness.state import (
    AgentAssessment,
    RunState,
    RunStatus,
    ToolCallRecord,
)
from unity_audit.harness.tools import ToolRegistry
from unity_audit.harness.tracing import TraceWriter


class HarnessRunner:
    """Runs the Agent Harness loop over AuditResult issues.

    Each issue is processed independently. The agent can call read-only tools
    and must submit a structured assessment for each issue.

    If anything goes wrong (model failure, policy violation, step limit,
    timeout), the harness falls back to the deterministic FixPlanner results.

    Multi-Agent support:
    - max_workers=1 (default): Sequential processing, backward compatible.
    - max_workers>1: Parallel processing via GroupCoordinator + ThreadPoolExecutor.
      Requires agent_factory to create per-worker AuditAgent instances.
    """

    def __init__(
        self,
        agent,  # AuditAgent instance (used for sequential mode)
        audit_result,  # AuditResult from deterministic scan
        max_steps: int = 12,
        trace_enabled: bool = True,
        tool_registry: ToolRegistry | None = None,
        max_workers: int = 1,
        agent_factory=None,  # Callable[[], AuditAgent] for parallel mode
    ):
        self.agent = agent
        self.audit_result = audit_result
        self.max_steps = max_steps
        self.trace_enabled = trace_enabled
        self.tool_registry = tool_registry or self._build_default_tool_registry()
        self.trace_writer: TraceWriter | None = None
        self.max_workers = max_workers
        self.agent_factory = agent_factory

        # Track tool retries: {(issue_id, tool_name, arguments_hash) -> count}
        self._retry_counts: dict[tuple[str, str, int], int] = {}

    def _build_default_tool_registry(self) -> ToolRegistry:
        """Build a ToolRegistry with the default read-only audit tools."""
        from unity_audit.harness.default_tools import build_default_tools

        registry = ToolRegistry()
        for tool_def in build_default_tools(self.audit_result):
            registry.register(tool_def)
        return registry

    def _hash_arguments(self, args: dict) -> int:
        """Hash tool arguments for deduplication.

        Handles unhashable types (list, dict) by converting to tuples.
        """
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
            # Fallback: hash the JSON representation
            import json
            return hash(json.dumps(args, sort_keys=True))

    def run(self, state: RunState, audit_result: AuditResult) -> RunState:
        """Run the agent harness over all pending issues.

        Issues are grouped by (rule_id, severity). Only one representative
        per group is sent to the LLM; its assessment is cloned for the rest.
        This avoids wasting API calls on hundreds of identical issues.

        Args:
            state: Initial RunState (possibly resumed from checkpoint).
            audit_result: The completed deterministic AuditResult.

        Returns:
            Final RunState (terminal status).
        """
        # Initialize trace
        if self.trace_enabled:
            self.trace_writer = TraceWriter(state.run_id)
            self.trace_writer.run_started(
                project_root=state.project_root,
                platform=state.platform,
            )

        target_issue_ids = list(state.pending_issue_ids)

        try:
            state.status = RunStatus.RUNNING.value

            # Phase 1: Group issues by (rule_id, severity, path_category, evidence level)
            # for dedup.
            # path_category ensures ReferenceImages/ and Scenes/ are NOT lumped
            # together — they need different risk assessments.
            # evidence_association_level keeps high-risk Read/Write evidence groups
            # separate, so direct/possible/none do not share one LLM assessment.
            pending_by_key: dict[tuple[str, str, str, str], list[str]] = {}
            for issue_id in list(state.pending_issue_ids):
                issue_data = self._get_issue_data(issue_id, audit_result)
                if issue_data is None:
                    state.add_error(f"Issue not found: {issue_id}")
                    state.complete_issue(issue_id)
                    continue
                key = (
                    issue_data["rule_id"],
                    issue_data["severity"],
                    issue_data["path_category"],
                    issue_data["evidence_association_level"],
                )
                pending_by_key.setdefault(key, []).append(issue_id)
            state.pending_issue_ids = []

            # Build groups list
            groups: list[tuple] = []
            for group_key, issue_ids in pending_by_key.items():
                representative_id = issue_ids[0]
                peers = list(issue_ids[1:])
                rep_data = self._get_issue_data(representative_id, audit_result)
                if rep_data is None:
                    state.complete_issue(representative_id)
                    for pid in peers:
                        state.complete_issue(pid)
                    continue
                groups.append((group_key, rep_data, peers))

            # Choose execution path: parallel or sequential
            if self.max_workers > 1 and self.agent_factory is not None:
                # ── Parallel path via GroupCoordinator ──
                from unity_audit.harness.coordinator import GroupCoordinator

                coordinator = GroupCoordinator(
                    agent_factory=self.agent_factory,
                    tool_registry=self.tool_registry,
                    audit_result=audit_result,
                    max_workers=self.max_workers,
                    max_steps_per_group=state.max_steps,
                    max_total_steps=max(0, state.max_steps - state.step_count),
                    trace_enabled=self.trace_enabled,
                )
                state = coordinator.dispatch(
                    groups=groups,
                    master_state=state,
                    trace_writer=self.trace_writer,
                )
            else:
                # ── Sequential path (backward compatible) ──
                for group_key, rep_data, peers in groups:
                    if state.step_count >= state.max_steps:
                        state.status = RunStatus.COMPLETED_WITH_FALLBACK.value
                        break

                    representative_id = rep_data["issue_id"]

                    assessment = None
                    try:
                        assessment = self._process_representative(
                            state, rep_data, audit_result)
                    except Exception as e:
                        state.add_error(f"Error processing {representative_id}: {e}")
                        if self.trace_writer:
                            self.trace_writer.fallback_used(str(e))

                    # Broadcast assessment to all peers
                    if assessment is not None:
                        state.add_assessment(assessment)
                        state.complete_issue(representative_id)

                        for peer_id in peers:
                            peer_data = self._get_issue_data(peer_id, audit_result)
                            peer_asset = peer_data["asset_path"] if peer_data else peer_id
                            peer_summary = (
                                f"[同组评估，基于 {representative_id} 的分析] "
                                f"{peer_asset}: {assessment.summary}"
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
                            state.add_assessment(peer_assessment)
                            state.complete_issue(peer_id)

                        if self.trace_writer:
                            self.trace_writer.model_responded(
                                state.step_count, representative_id,
                                "broadcast", tool_name=None,
                            )
                    else:
                        # Representative failed — mark all as fallback
                        state.complete_issue(representative_id)
                        for peer_id in peers:
                            state.complete_issue(peer_id)

            assessed_issue_ids = {a.issue_id for a in state.agent_assessments}
            if all(issue_id in assessed_issue_ids for issue_id in target_issue_ids):
                state.status = RunStatus.COMPLETED.value
            else:
                state.status = RunStatus.COMPLETED_WITH_FALLBACK.value

        except Exception as e:
            state.status = RunStatus.COMPLETED_WITH_FALLBACK.value
            state.add_error(f"Harness error: {e}")
            if self.trace_writer:
                self.trace_writer.fallback_used(f"Harness error: {e}")

        finally:
            if self.trace_writer:
                self.trace_writer.run_completed(
                    step=state.step_count,
                    status=state.status,
                    assessments_count=len(state.agent_assessments),
                    tool_calls=len(state.tool_results),
                )

        return state

    def _process_representative(self, state: RunState, issue_data: dict,
                                  audit_result: AuditResult) -> AgentAssessment | None:
        """Process a representative issue through the agent loop.

        Returns the AgentAssessment on success, None on failure/fallback.
        Does NOT add the assessment to state — caller handles broadcasting.
        """
        return self._process_issue_inner(state, issue_data, audit_result,
                                          add_to_state=False)

    def _process_issue(self, state: RunState, issue_data: dict,
                       audit_result: AuditResult):
        """Process a single issue through the agent loop (standalone mode).

        Adds the assessment to state directly. Used by tests and non-grouped mode.
        """
        self._process_issue_inner(state, issue_data, audit_result, add_to_state=True)

    def _process_issue_inner(self, state: RunState, issue_data: dict,
                              audit_result: AuditResult,
                              add_to_state: bool = True) -> AgentAssessment | None:
        """Core issue processing loop.

        Args:
            state: Current RunState.
            issue_data: Issue context dict.
            audit_result: Full deterministic result.
            add_to_state: If True, adds assessment to state directly.
                          If False, returns the assessment instead.

        Returns:
            AgentAssessment if add_to_state=False and assessment was produced,
            None otherwise.
        """
        issue_id = issue_data["issue_id"]
        state.current_issue_id = issue_id
        registry_tool_names = {t.name for t in self.tool_registry.list_tools()}
        existing_result_ids = {r.tool_result_id for r in state.tool_results}
        results_before = len(state.tool_results)  # Track per-issue progress

        # Inner loop for this issue (steps until finish or max)
        while state.step_count < state.max_steps:
            # Check if we're already terminal
            if state.is_terminal():
                return

            # 1. Get action from model
            state.increment_step()
            step = state.step_count
            remaining = state.max_steps - step

            # Dynamic tool list: prevent redundant calls and force submission
            all_tool_defs = self.tool_registry.list_tools()

            # Track which info-gathering tools have succeeded for THIS issue
            successful_tools = set()
            for tr in state.tool_results[results_before:]:
                if tr.result.get("ok"):
                    successful_tools.add(tr.tool_name)

            # Progressive tool restriction: narrow choices as info is gathered
            info_tools_used = successful_tools & {
                "get_issue_detail", "get_asset_info", "search_asset_code",
                "list_project_assets", "read_code_context",
                "trace_prefab_references",
            }
            if remaining <= 2:
                # Only allow submit_assessment when running out of steps
                tool_defs = [t for t in all_tool_defs if t.name == "submit_assessment"]
            elif len(info_tools_used) >= 3:
                # Three info-gathering calls — enough, force assessment
                tool_defs = [t for t in all_tool_defs if t.name == "submit_assessment"]
            elif "get_issue_detail" in successful_tools:
                # Already got issue details — allow deep-dive tools + submit
                tool_defs = [t for t in all_tool_defs
                             if t.name in ("submit_assessment", "search_asset_code",
                                           "get_asset_info", "read_code_context",
                                           "trace_prefab_references")]
            else:
                tool_defs = all_tool_defs

            try:
                if self.trace_writer:
                    self.trace_writer.model_requested(
                        step, issue_id, self.agent.model_name,
                        messages_count=1,  # Simplified
                        tool_count=len(tool_defs),
                    )

                action = self.agent.get_action(
                    issue_data=issue_data,
                    tool_defs=tool_defs,
                    tool_results=state.tool_results[results_before:],
                    step=step,
                    max_steps=self.max_steps,
                    rule_id=issue_data.get("rule_id"),
                )
            except Exception as e:
                if self.trace_writer:
                    self.trace_writer.guardrail_triggered(
                        step, issue_id, f"Model error: {e}"
                    )
                state.add_error(f"Model error for {issue_id}: {e}")
                return  # Fallback to deterministic for this issue

            # 2. Validate action structure
            valid, error_msg = validate_action(
                action, registry_tool_names, existing_result_ids,
                original_issue=issue_data,
            )
            if not valid:
                if self.trace_writer:
                    self.trace_writer.guardrail_triggered(
                        step, issue_id, f"Validation: {error_msg}"
                    )
                state.add_error(f"Policy violation for {issue_id}: {error_msg}")
                return

            # 3. Trace the model response
            action_type = action["action"]
            if self.trace_writer:
                self.trace_writer.model_responded(
                    step, issue_id, action_type,
                    tool_name=action.get("tool_name"),
                )

            # 4. Execute tool or finish
            if action_type == "finish":
                assessment_dict = action["assessment"]
                agent_assessment = AgentAssessment(
                    issue_id=assessment_dict["issue_id"],
                    risk_level=assessment_dict["risk_level"],
                    recommended_action=assessment_dict["recommended_action"],
                    confidence=assessment_dict["confidence"],
                    summary=assessment_dict["summary"],
                    evidence_refs=assessment_dict.get("evidence_refs", []),
                    needs_human_review=assessment_dict.get("needs_human_review", False),
                    usage_context=assessment_dict.get("usage_context", "unknown"),
                    evidence_strength=assessment_dict.get("evidence_strength", "none"),
                    fix_plan=assessment_dict.get("fix_plan"),
                )
                valid_assessment, assessment_error = validate_assessment_guardrails(
                    issue_data,
                    agent_assessment,
                    state.tool_results[results_before:],
                )
                if not valid_assessment:
                    if self.trace_writer:
                        self.trace_writer.guardrail_triggered(
                            step, issue_id, f"Assessment: {assessment_error}"
                        )
                    state.add_error(f"Assessment rejected for {issue_id}: {assessment_error}")
                    return
                if add_to_state:
                    state.add_assessment(agent_assessment)
                    return  # Issue done (standalone)
                else:
                    return agent_assessment  # Return for broadcasting

            elif action_type == "call_tool":
                tool_name = action["tool_name"]
                arguments = action["arguments"]

                # Check retry limit
                args_hash = self._hash_arguments(arguments)
                retry_key = (issue_id, tool_name, args_hash)
                retry_count = self._retry_counts.get(retry_key, 0)
                if retry_count > 1:
                    # Already retried once with same params
                    if self.trace_writer:
                        self.trace_writer.guardrail_triggered(
                            step, issue_id,
                            f"Retry limit exceeded for {tool_name}"
                        )
                    continue  # Skip, maybe model tries a different tool

                # Execute tool
                if self.trace_writer:
                    self.trace_writer.tool_requested(
                        step, issue_id, tool_name, arguments
                    )

                result = self.tool_registry.execute(tool_name, arguments)

                # Generate tool result ID
                result.tool_result_id = f"tr_{uuid.uuid4().hex[:8]}"

                # Record result
                record = ToolCallRecord(
                    tool_result_id=result.tool_result_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result.to_dict(),
                    step=step,
                )
                state.add_tool_result(record)
                existing_result_ids.add(result.tool_result_id)

                # Check for submit_assessment — this completes the issue
                if tool_name == "submit_assessment" and result.ok:
                    if self.trace_writer:
                        self.trace_writer.tool_completed(
                            step, issue_id, tool_name,
                            result.tool_result_id, True,
                        )
                    assessment = AgentAssessment(
                        issue_id=arguments.get("issue_id", issue_id),
                        risk_level=arguments.get("risk_level", "medium"),
                        recommended_action=arguments.get("recommended_action", "manual_confirm_required"),
                        confidence=arguments.get("confidence", 0.5),
                        summary=arguments.get("summary", ""),
                        evidence_refs=arguments.get("evidence_refs", []),
                        needs_human_review=arguments.get("needs_human_review", False),
                        usage_context=arguments.get("usage_context", "unknown"),
                        evidence_strength=arguments.get("evidence_strength", "none"),
                        fix_plan=arguments.get("fix_plan"),
                    )
                    valid_payload, payload_error = validate_assessment_payload(
                        arguments,
                        existing_result_ids - {result.tool_result_id},
                        original_issue=issue_data,
                    )
                    if not valid_payload:
                        if self.trace_writer:
                            self.trace_writer.guardrail_triggered(
                                step, issue_id, f"Assessment: {payload_error}"
                            )
                        state.add_error(
                            f"Assessment rejected for {issue_id}: {payload_error}"
                        )
                        return
                    valid_assessment, assessment_error = validate_assessment_guardrails(
                        issue_data,
                        assessment,
                        state.tool_results[results_before:],
                    )
                    if not valid_assessment:
                        if self.trace_writer:
                            self.trace_writer.guardrail_triggered(
                                step, issue_id, f"Assessment: {assessment_error}"
                            )
                        state.add_error(
                            f"Assessment rejected for {issue_id}: {assessment_error}"
                        )
                        return
                    if add_to_state:
                        state.add_assessment(assessment)
                        return  # Issue done (standalone mode, void return)
                    else:
                        return assessment  # Return to caller for broadcasting

                if result.ok:
                    if self.trace_writer:
                        self.trace_writer.tool_completed(
                            step, issue_id, tool_name,
                            result.tool_result_id, True,
                        )
                    # Clear retry count on success
                    self._retry_counts.pop(retry_key, None)
                else:
                    if self.trace_writer:
                        self.trace_writer.tool_failed(
                            step, issue_id, tool_name,
                            result.error_code or "TOOL_ERROR",
                            result.message,
                        )
                    # Track retry
                    self._retry_counts[retry_key] = retry_count + 1

        # If we exit the loop without finishing, fallback
        if state.step_count >= state.max_steps:
            state.status = RunStatus.COMPLETED_WITH_FALLBACK.value

    @staticmethod
    def _classify_path(asset_path: str) -> str:
        """Classify an asset path into a broad category for grouping.

        Returns one of: reference_images, ui, editor_only, third_party,
        character, audio, scene, prefab, resource, texture, unknown.
        """
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

    def _get_issue_data(self, issue_id: str,
                        audit_result: AuditResult) -> dict | None:
        """Get issue data from the audit result, enriched with fix decision."""
        for issue in audit_result.issues:
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
                    "evidence_association_level": "none",
                }
                evidence = audit_result.evidence_map.get(issue_id)
                if evidence is not None:
                    data["evidence_association_level"] = evidence.association_level
                # Include deterministic fix decision so agent can skip get_issue_detail
                decision = None
                for d in audit_result.fix_decisions:
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
