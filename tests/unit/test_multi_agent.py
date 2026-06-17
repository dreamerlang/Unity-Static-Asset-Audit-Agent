"""Unit tests for Multi-Agent v0.1 features.

Tests cover:
- RunState.merge() correctness
- GroupWorker with FakeModelClient
- GroupCoordinator parallel dispatch + failure isolation
- Specialized prompt routing
- End-to-end parallel execution with multiple groups
- Thread safety (no race conditions)
"""

import threading

from unity_audit.agents.audit_agent import AuditAgent
from unity_audit.agents.model_client import FakeModelClient
from unity_audit.agents.prompts import build_system_prompt, SYSTEM_PROMPT
from unity_audit.agents.specialized_prompts import (
    AUDIO_AGENT_PROMPT,
    PREFAB_AGENT_PROMPT,
    SHADER_AGENT_PROMPT,
    TEXTURE_AGENT_PROMPT,
    get_prompt_for_rule,
)
from unity_audit.application.models import AuditResult
from unity_audit.harness.coordinator import GroupCoordinator
from unity_audit.harness.runner import HarnessRunner
from unity_audit.harness.state import (
    AgentAssessment,
    RunState,
    RunStatus,
    ToolCallRecord,
)
from unity_audit.harness.tools import ToolDef, ToolRegistry
from unity_audit.harness.tracing import TraceWriter
from unity_audit.harness.worker import GroupWorker, WorkerResult
from unity_audit.rules.engine import Issue


# ── Helpers ──

def _make_issue(issue_id, rule_id, severity, asset_path):
    """Create a minimal Issue for testing."""
    return Issue(
        issue_id=issue_id,
        rule_id=rule_id,
        severity=severity,
        asset_path=asset_path,
        title=f"Issue {issue_id}",
        message=f"Message for {issue_id}",
        evidence={},
        suggestion="Review.",
    )


def _make_audit_result(issues):
    """Create a minimal AuditResult from a list of Issues."""
    return AuditResult(
        project_root="/fake/project",
        platform="Android",
        issues=issues,
    )


def _make_tool_registry():
    """Build a ToolRegistry with basic tools for testing."""
    registry = ToolRegistry()

    def get_issue_detail(issue_id: str) -> dict:
        return {"issue_id": issue_id, "found": True}

    def submit_assessment(**kwargs) -> dict:
        return {"ok": True, "assessment": kwargs}

    registry.register(ToolDef(
        name="get_issue_detail",
        description="Get issue details",
        func=get_issue_detail,
        parameters={
            "type": "object",
            "properties": {"issue_id": {"type": "string"}},
            "required": ["issue_id"],
        },
    ))
    registry.register(ToolDef(
        name="submit_assessment",
        description="Submit final assessment",
        func=submit_assessment,
        parameters={
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "risk_level": {"type": "string"},
                "recommended_action": {"type": "string"},
                "confidence": {"type": "number"},
                "summary": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["issue_id", "risk_level", "recommended_action", "confidence", "summary"],
        },
    ))
    return registry


def _make_fake_agent(actions):
    """Create an AuditAgent with a FakeModelClient using preset actions."""
    fake = FakeModelClient("fake-test", actions=actions)
    return AuditAgent(model_client=fake)


# ═══════════════════════════════════════════════════════════════════
# TestRunStateMerge
# ═══════════════════════════════════════════════════════════════════

class TestRunStateMerge:
    """Tests for RunState.merge() method."""

    def test_merge_basic(self):
        """Merging two RunStates combines their lists."""
        s1 = RunState(
            run_id="run_1",
            project_root="/fake",
            platform="Android",
        )
        s1.add_tool_result(ToolCallRecord(
            tool_result_id="tr_001",
            tool_name="get_issue_detail",
            arguments={"issue_id": "A"},
            result={"ok": True},
        ))
        s1.add_assessment(AgentAssessment(
            issue_id="A",
            risk_level="low",
            recommended_action="auto_fix_candidate",
            confidence=0.9,
            summary="Done A",
        ))

        s2 = RunState(
            run_id="run_2",
            project_root="/fake",
            platform="Android",
        )
        s2.add_tool_result(ToolCallRecord(
            tool_result_id="tr_002",
            tool_name="get_issue_detail",
            arguments={"issue_id": "B"},
            result={"ok": True},
        ))
        s2.add_assessment(AgentAssessment(
            issue_id="B",
            risk_level="medium",
            recommended_action="manual_confirm_required",
            confidence=0.7,
            summary="Done B",
        ))
        s2.add_error("Something went wrong")

        s1.merge(s2)

        assert len(s1.tool_results) == 2
        assert len(s1.agent_assessments) == 2
        assert len(s1.errors) == 1
        assert s1.tool_results[0].tool_result_id == "tr_001"
        assert s1.tool_results[1].tool_result_id == "tr_002"

    def test_merge_dedup_completed_issues(self):
        """Merging should not duplicate completed_issue_ids."""
        s1 = RunState(run_id="r1", project_root="/f", platform="A")
        s1.complete_issue("ISSUE_1")

        s2 = RunState(run_id="r2", project_root="/f", platform="A")
        s2.complete_issue("ISSUE_1")  # Same issue
        s2.complete_issue("ISSUE_2")

        s1.merge(s2)

        # ISSUE_1 should appear only once
        assert s1.completed_issue_ids.count("ISSUE_1") == 1
        assert "ISSUE_2" in s1.completed_issue_ids

    def test_merge_step_counts(self):
        """Merging should sum step counts."""
        s1 = RunState(run_id="r1", project_root="/f", platform="A")
        s1.step_count = 3

        s2 = RunState(run_id="r2", project_root="/f", platform="A")
        s2.step_count = 5

        s1.merge(s2)
        assert s1.step_count == 8


# ═══════════════════════════════════════════════════════════════════
# TestGroupWorker
# ═══════════════════════════════════════════════════════════════════

class TestGroupWorker:
    """Tests for GroupWorker with FakeModelClient."""

    def test_worker_finish_immediately(self):
        """Worker with a finish-first model returns an assessment."""
        issue = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/test.png")
        audit_result = _make_audit_result([issue])

        agent = _make_fake_agent([{
            "action": "finish",
            "assessment": {
                "issue_id": "TEX_RW_0",
                "risk_level": "low",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.85,
                "summary": "Safe to fix.",
                "evidence_refs": [],
                "needs_human_review": False,
            },
        }])

        worker = GroupWorker(
            agent=agent,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_steps=12,
            trace_enabled=False,
            worker_id=0,
        )

        rep_data = {
            "issue_id": "TEX_RW_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/test.png",
            "title": "Test",
            "message": "Test",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }

        result = worker.run(
            group_key=("TEX_READ_WRITE_ENABLED", "high", "texture"),
            representative_data=rep_data,
            peer_ids=[],
        )

        assert result.success
        assert result.assessment is not None
        assert result.assessment.recommended_action == "auto_fix_candidate"
        assert result.assessment.confidence == 0.85

    def test_worker_call_tool_then_finish(self):
        """Worker calls a tool then finishes."""
        issue = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/test.png")
        audit_result = _make_audit_result([issue])

        agent = _make_fake_agent([
            {
                "action": "call_tool",
                "tool_name": "get_issue_detail",
                "arguments": {"issue_id": "TEX_RW_0"},
                "reason": "Need details",
            },
            {
                "action": "finish",
                "assessment": {
                    "issue_id": "TEX_RW_0",
                    "risk_level": "medium",
                    "recommended_action": "manual_confirm_required",
                    "confidence": 0.7,
                    "summary": "Needs review.",
                    "evidence_refs": [],
                    "needs_human_review": True,
                },
            },
        ])

        worker = GroupWorker(
            agent=agent,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_steps=12,
            trace_enabled=False,
            worker_id=1,
        )

        rep_data = {
            "issue_id": "TEX_RW_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/test.png",
            "title": "Test",
            "message": "Test",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }

        result = worker.run(
            group_key=("TEX_READ_WRITE_ENABLED", "high", "texture"),
            representative_data=rep_data,
            peer_ids=["TEX_RW_1"],
        )

        assert result.success
        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_name == "get_issue_detail"

    def test_worker_max_steps_fallback(self):
        """Worker exceeding max_steps returns fallback result."""
        issue = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/test.png")
        audit_result = _make_audit_result([issue])

        # Model that keeps calling tools forever
        actions = [
            {"action": "call_tool", "tool_name": "get_issue_detail",
             "arguments": {"issue_id": "TEX_RW_0"}, "reason": f"Step {i}"}
            for i in range(20)
        ]
        agent = _make_fake_agent(actions)

        worker = GroupWorker(
            agent=agent,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_steps=3,  # Very limited
            trace_enabled=False,
            worker_id=0,
        )

        rep_data = {
            "issue_id": "TEX_RW_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/test.png",
            "title": "Test",
            "message": "Test",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }

        result = worker.run(
            group_key=("TEX_READ_WRITE_ENABLED", "high", "texture"),
            representative_data=rep_data,
            peer_ids=[],
        )

        assert result.fallback
        assert result.assessment is None

    def test_worker_unknown_tool_guardrail(self):
        """Worker with unknown tool triggers guardrail and falls back."""
        issue = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/test.png")
        audit_result = _make_audit_result([issue])

        agent = _make_fake_agent([
            {"action": "call_tool", "tool_name": "delete_all",
             "arguments": {}, "reason": "Test"},
            {"action": "finish", "assessment": {
                "issue_id": "TEX_RW_0", "risk_level": "low",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.5, "summary": "Should not reach.",
            }},
        ])

        worker = GroupWorker(
            agent=agent,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_steps=12,
            trace_enabled=False,
            worker_id=0,
        )

        rep_data = {
            "issue_id": "TEX_RW_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/test.png",
            "title": "Test",
            "message": "Test",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }

        result = worker.run(
            group_key=("TEX_READ_WRITE_ENABLED", "high", "texture"),
            representative_data=rep_data,
            peer_ids=[],
        )

        assert result.fallback
        assert result.assessment is None


# ═══════════════════════════════════════════════════════════════════
# TestGroupCoordinator
# ═══════════════════════════════════════════════════════════════════

class TestGroupCoordinator:
    """Tests for GroupCoordinator parallel dispatch."""

    def test_coordinator_sequential_single_group(self):
        """Coordinator with max_workers=1 processes a single group."""
        issue = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/test.png")
        audit_result = _make_audit_result([issue])

        def agent_factory():
            return _make_fake_agent([{
                "action": "finish",
                "assessment": {
                    "issue_id": "TEX_RW_0",
                    "risk_level": "low",
                    "recommended_action": "do_not_fix",
                    "confidence": 0.9,
                    "summary": "Reference image, safe to ignore.",
                    "evidence_refs": [],
                    "needs_human_review": False,
                },
            }])

        coordinator = GroupCoordinator(
            agent_factory=agent_factory,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_workers=1,
            max_steps_per_group=12,
            trace_enabled=False,
        )

        rep_data = {
            "issue_id": "TEX_RW_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/test.png",
            "title": "Test",
            "message": "Test",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }

        state = RunState(
            run_id="coord_test",
            project_root="/fake",
            platform="Android",
            max_steps=12,
        )

        groups = [(("TEX_READ_WRITE_ENABLED", "high", "texture"), rep_data, [])]
        final_state = coordinator.dispatch(groups, state)

        assert final_state.status == RunStatus.COMPLETED.value
        assert len(final_state.agent_assessments) == 1
        assert final_state.agent_assessments[0].recommended_action == "do_not_fix"

    def test_coordinator_parallel_two_groups(self):
        """Coordinator with max_workers=2 processes two groups in parallel."""
        issue_a = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/a.png")
        issue_b = _make_issue("AUD_LONG_0", "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD", "high", "Audio/sfx.wav")
        audit_result = _make_audit_result([issue_a, issue_b])

        # Use a counter to give different assessments per group
        counter = {"value": 0}

        def agent_factory():
            idx = counter["value"]
            counter["value"] += 1
            actions = [
                {
                    "action": "finish",
                    "assessment": {
                        "issue_id": f"ISSUE_{idx}",
                        "risk_level": "low",
                        "recommended_action": "auto_fix_candidate",
                        "confidence": 0.8 + idx * 0.05,
                        "summary": f"Assessment number {idx} — detailed analysis.",
                        "evidence_refs": [],
                        "needs_human_review": False,
                    },
                },
            ]
            return _make_fake_agent(actions)

        coordinator = GroupCoordinator(
            agent_factory=agent_factory,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_workers=2,
            max_steps_per_group=12,
            trace_enabled=False,
        )

        rep_data_a = {
            "issue_id": "TEX_RW_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/a.png",
            "title": "A",
            "message": "A",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }
        rep_data_b = {
            "issue_id": "AUD_LONG_0",
            "rule_id": "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD",
            "severity": "high",
            "asset_path": "Audio/sfx.wav",
            "title": "B",
            "message": "B",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": False,
            "path_category": "audio",
        }

        state = RunState(
            run_id="parallel_test",
            project_root="/fake",
            platform="Android",
            max_steps=12,
        )

        groups = [
            (("TEX_READ_WRITE_ENABLED", "high", "texture"), rep_data_a, []),
            (("AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD", "high", "audio"), rep_data_b, []),
        ]
        final_state = coordinator.dispatch(groups, state)

        assert final_state.status == RunStatus.COMPLETED.value
        assert len(final_state.agent_assessments) == 2

    def test_coordinator_worker_failure_isolation(self):
        """One failing worker should not affect other workers."""
        issue_a = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/a.png")
        issue_b = _make_issue("TEX_RW_1", "TEX_READ_WRITE_ENABLED", "high", "Textures/b.png")
        audit_result = _make_audit_result([issue_a, issue_b])

        call_count = {"value": 0}

        def agent_factory():
            idx = call_count["value"]
            call_count["value"] += 1
            if idx == 0:
                # First worker: use unknown tool → fallback
                return _make_fake_agent([
                    {"action": "call_tool", "tool_name": "nonexistent",
                     "arguments": {}, "reason": "Test"},
                ])
            else:
                # Second worker: succeeds
                return _make_fake_agent([{
                    "action": "finish",
                    "assessment": {
                        "issue_id": "TEX_RW_1",
                        "risk_level": "low",
                        "recommended_action": "auto_fix_candidate",
                        "confidence": 0.8,
                        "summary": "Success.",
                        "evidence_refs": [],
                        "needs_human_review": False,
                    },
                }])

        coordinator = GroupCoordinator(
            agent_factory=agent_factory,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_workers=2,
            max_steps_per_group=12,
            trace_enabled=False,
        )

        rep_a = {
            "issue_id": "TEX_RW_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/a.png",
            "title": "A",
            "message": "A",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }
        rep_b = {
            "issue_id": "TEX_RW_1",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/b.png",
            "title": "B",
            "message": "B",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }

        state = RunState(run_id="isolation_test", project_root="/fake", platform="Android")

        groups = [
            (("TEX_READ_WRITE_ENABLED", "high", "texture"), rep_a, []),
            (("TEX_READ_WRITE_ENABLED", "high", "texture"), rep_b, []),
        ]
        final_state = coordinator.dispatch(groups, state)

        # One group should have an assessment (the successful one)
        assert len(final_state.agent_assessments) >= 1
        # The failing group's issue should be completed (fallback)
        assert "TEX_RW_0" in final_state.completed_issue_ids
        assert "TEX_RW_1" in final_state.completed_issue_ids

    def test_coordinator_peer_broadcast(self):
        """Coordinator broadcasts assessment to peer issues in the same group."""
        issue_rep = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/a.png")
        # Peer issues (same group, different asset paths)
        audit_result = _make_audit_result([issue_rep])

        def agent_factory():
            return _make_fake_agent([{
                "action": "finish",
                "assessment": {
                    "issue_id": "TEX_RW_0",
                    "risk_level": "low",
                    "recommended_action": "do_not_fix",
                    "confidence": 0.9,
                    "summary": "Reference image, safe to ignore.",
                    "evidence_refs": [],
                    "needs_human_review": False,
                },
            }])

        coordinator = GroupCoordinator(
            agent_factory=agent_factory,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_workers=1,
            max_steps_per_group=12,
            trace_enabled=False,
        )

        rep_data = {
            "issue_id": "TEX_RW_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/a.png",
            "title": "A",
            "message": "A",
            "evidence": {},
            "suggestion": "Fix",
            "auto_fixable": True,
            "path_category": "texture",
        }

        state = RunState(run_id="broadcast_test", project_root="/fake", platform="Android")

        groups = [(("TEX_READ_WRITE_ENABLED", "high", "texture"), rep_data, ["TEX_RW_1", "TEX_RW_2"])]
        final_state = coordinator.dispatch(groups, state)

        # Representative + 2 peers = 3 assessments
        assert len(final_state.agent_assessments) == 3

        # Peer assessments should reference the representative
        peer_assessments = [
            a for a in final_state.agent_assessments
            if a.issue_id in ("TEX_RW_1", "TEX_RW_2")
        ]
        assert len(peer_assessments) == 2
        for pa in peer_assessments:
            assert "同组评估" in pa.summary
            assert pa.recommended_action == "do_not_fix"
            assert pa.risk_level == "low"

    def test_coordinator_empty_groups(self):
        """Coordinator with no groups returns completed immediately."""
        audit_result = _make_audit_result([])

        coordinator = GroupCoordinator(
            agent_factory=lambda: None,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_workers=2,
        )

        state = RunState(run_id="empty_test", project_root="/fake", platform="Android")
        final_state = coordinator.dispatch([], state)

        assert final_state.status == RunStatus.COMPLETED.value


# ═══════════════════════════════════════════════════════════════════
# TestPromptRouting
# ═══════════════════════════════════════════════════════════════════

class TestPromptRouting:
    """Tests for specialized prompt routing."""

    def test_texture_rules_get_texture_prompt(self):
        """TEX_* rules should route to TEXTURE_AGENT_PROMPT."""
        assert get_prompt_for_rule("TEX_READ_WRITE_ENABLED") == TEXTURE_AGENT_PROMPT
        assert get_prompt_for_rule("TEX_UI_MIPMAP_ENABLED") == TEXTURE_AGENT_PROMPT
        assert get_prompt_for_rule("TEX_UI_MAX_SIZE_TOO_LARGE") == TEXTURE_AGENT_PROMPT
        assert get_prompt_for_rule("TEX_NPOT_DETECTED") == TEXTURE_AGENT_PROMPT

    def test_audio_rules_get_audio_prompt(self):
        """AUD_* rules should route to AUDIO_AGENT_PROMPT."""
        assert get_prompt_for_rule("AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD") == AUDIO_AGENT_PROMPT
        assert get_prompt_for_rule("AUD_STEREO_SFX") == AUDIO_AGENT_PROMPT

    def test_prefab_rules_get_prefab_prompt(self):
        """PREFAB_* and UI_* rules should route to PREFAB_AGENT_PROMPT."""
        assert get_prompt_for_rule("PREFAB_MISSING_SCRIPT") == PREFAB_AGENT_PROMPT
        assert get_prompt_for_rule("UI_TOO_MANY_GRAPHIC_RAYCASTERS") == PREFAB_AGENT_PROMPT

    def test_shader_rules_get_shader_prompt(self):
        """SHADER_* and MAT_* rules should route to SHADER_AGENT_PROMPT."""
        assert get_prompt_for_rule("SHADER_VARIANTS") == SHADER_AGENT_PROMPT
        assert get_prompt_for_rule("MAT_REDUNDANT_PROPERTIES") == SHADER_AGENT_PROMPT

    def test_unknown_rule_returns_none(self):
        """Unknown rule IDs should return None (use default SYSTEM_PROMPT)."""
        assert get_prompt_for_rule("UNKNOWN_RULE") is None
        assert get_prompt_for_rule("") is None

    def test_build_system_prompt_uses_specialized_prompt(self):
        """build_system_prompt with rule_id should use specialized prompt."""
        prompt = build_system_prompt(
            issue_context='{"test": 1}',
            tool_descriptions="Tools here",
            tool_results_context="No results",
            step=1,
            max_steps=12,
            rule_id="TEX_READ_WRITE_ENABLED",
        )
        # Should contain texture-specific content
        assert "Mipmaps" in prompt or "texture" in prompt.lower()

    def test_build_system_prompt_falls_back_to_default(self):
        """build_system_prompt with unknown rule_id uses default prompt."""
        prompt = build_system_prompt(
            issue_context='{"test": 1}',
            tool_descriptions="Tools here",
            tool_results_context="No results",
            step=1,
            max_steps=12,
            rule_id="UNKNOWN_RULE_XYZ",
        )
        # Should contain the default SYSTEM_PROMPT content (not specialized)
        assert "Unity Asset Audit Agent" in prompt

    def test_specialized_prompts_contain_domain_keywords(self):
        """Each specialized prompt should contain domain-specific keywords."""
        assert "mipmap" in TEXTURE_AGENT_PROMPT.lower()
        assert "decompress" in AUDIO_AGENT_PROMPT.lower()
        assert "missing script" in PREFAB_AGENT_PROMPT.lower()
        assert "variant" in SHADER_AGENT_PROMPT.lower()

    def test_audit_agent_uses_specialized_prompt(self):
        """AuditAgent.get_action() should pass rule_id and trigger specialized prompt."""
        agent = _make_fake_agent([{
            "action": "finish",
            "assessment": {
                "issue_id": "TEX_0",
                "risk_level": "low",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.8,
                "summary": "Done.",
            },
        }])

        issue_data = {
            "issue_id": "TEX_0",
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "severity": "high",
            "asset_path": "Textures/test.png",
        }

        action = agent.get_action(
            issue_data=issue_data,
            tool_defs=[],
            tool_results=[],
            step=1,
            max_steps=12,
        )
        # Should get the finish action — specialized prompt is used internally
        assert action["action"] == "finish"


# ═══════════════════════════════════════════════════════════════════
# TestHarnessRunnerParallel
# ═══════════════════════════════════════════════════════════════════

class TestHarnessRunnerParallel:
    """End-to-end tests for HarnessRunner with parallel execution."""

    def test_runner_sequential_backward_compatible(self):
        """HarnessRunner with max_workers=1 should behave same as before."""
        issue = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/test.png")
        audit_result = _make_audit_result([issue])

        agent = _make_fake_agent([{
            "action": "finish",
            "assessment": {
                "issue_id": "TEX_RW_0",
                "risk_level": "low",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.85,
                "summary": "Safe.",
                "evidence_refs": [],
                "needs_human_review": False,
            },
        }])

        runner = HarnessRunner(
            agent=agent,
            audit_result=audit_result,
            max_steps=12,
            trace_enabled=False,
            max_workers=1,
            agent_factory=None,
        )

        state = RunState(
            run_id="backward_compat",
            project_root="/fake",
            platform="Android",
            pending_issue_ids=["TEX_RW_0"],
            max_steps=12,
        )

        final_state = runner.run(state, audit_result)
        assert final_state.status == RunStatus.COMPLETED.value
        assert len(final_state.agent_assessments) == 1

    def test_runner_parallel_two_groups(self):
        """HarnessRunner with max_workers=2 and agent_factory uses parallel path."""
        issue_a = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high", "Textures/a.png")
        issue_b = _make_issue("AUD_LONG_0", "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD", "high", "Audio/sfx.wav")
        audit_result = _make_audit_result([issue_a, issue_b])

        counter = {"value": 0}

        def agent_factory():
            idx = counter["value"]
            counter["value"] += 1
            return _make_fake_agent([{
                "action": "finish",
                "assessment": {
                    "issue_id": f"GROUP_{idx}",
                    "risk_level": "low",
                    "recommended_action": "auto_fix_candidate",
                    "confidence": 0.8,
                    "summary": f"Group {idx} done.",
                    "evidence_refs": [],
                    "needs_human_review": False,
                },
            }])

        # Use the default agent for sequential fallback (won't be used in parallel)
        default_agent = _make_fake_agent([])

        runner = HarnessRunner(
            agent=default_agent,
            audit_result=audit_result,
            max_steps=24,
            trace_enabled=False,
            max_workers=2,
            agent_factory=agent_factory,
        )

        state = RunState(
            run_id="parallel_runner",
            project_root="/fake",
            platform="Android",
            pending_issue_ids=["TEX_RW_0", "AUD_LONG_0"],
            max_steps=24,
        )

        final_state = runner.run(state, audit_result)
        assert final_state.status == RunStatus.COMPLETED.value
        # Two groups → 2 assessments
        assert len(final_state.agent_assessments) == 2

    def test_runner_parallel_different_path_categories(self):
        """Issues with same rule_id but different paths get separate groups."""
        issue_a = _make_issue("TEX_RW_0", "TEX_READ_WRITE_ENABLED", "high",
                              "ReferenceImages/Linear/test.png")
        issue_b = _make_issue("TEX_RW_1", "TEX_READ_WRITE_ENABLED", "high",
                              "Scenes/UI/button.png")
        issue_c = _make_issue("TEX_RW_2", "TEX_READ_WRITE_ENABLED", "high",
                              "ReferenceImages/Metal/other.png")  # Same group as issue_a
        audit_result = _make_audit_result([issue_a, issue_b, issue_c])

        call_count = {"value": 0}

        def agent_factory():
            idx = call_count["value"]
            call_count["value"] += 1
            return _make_fake_agent([{
                "action": "finish",
                "assessment": {
                    "issue_id": f"ISSUE_{idx}",
                    "risk_level": "low" if idx == 0 else "medium",
                    "recommended_action": "do_not_fix" if idx == 0 else "manual_confirm_required",
                    "confidence": 0.85,
                    "summary": f"Assessment number {idx} — detailed analysis.",
                    "evidence_refs": [],
                    "needs_human_review": idx == 1,
                },
            }])

        default_agent = _make_fake_agent([])
        runner = HarnessRunner(
            agent=default_agent,
            audit_result=audit_result,
            max_steps=24,
            trace_enabled=False,
            max_workers=2,
            agent_factory=agent_factory,
        )

        state = RunState(
            run_id="parallel_grouping",
            project_root="/fake",
            platform="Android",
            pending_issue_ids=["TEX_RW_0", "TEX_RW_1", "TEX_RW_2"],
            max_steps=24,
        )

        final_state = runner.run(state, audit_result)
        # 2 groups (reference_images + ui) → 2 agent calls → 3 total assessments
        # (1 rep + 1 peer for reference_images, 1 rep for ui)
        assert len(final_state.agent_assessments) == 3


# ═══════════════════════════════════════════════════════════════════
# TestThreadSafety
# ═══════════════════════════════════════════════════════════════════

class TestThreadSafety:
    """Thread safety tests for parallel execution."""

    def test_runstate_merge_thread_safety(self):
        """Concurrent RunState.merge() calls should not lose data."""
        import random

        master = RunState(run_id="ts_test", project_root="/f", platform="A")
        lock = threading.Lock()
        errors = []

        def worker(n):
            try:
                for _ in range(10):
                    s = RunState(run_id=f"w{n}", project_root="/f", platform="A")
                    s.add_tool_result(ToolCallRecord(
                        tool_result_id=f"tr_w{n}_{random.randint(0, 99999)}",
                        tool_name="get_issue_detail",
                        arguments={},
                        result={"ok": True},
                    ))
                    s.add_assessment(AgentAssessment(
                        issue_id=f"ISSUE_W{n}",
                        risk_level="low",
                        recommended_action="auto_fix_candidate",
                        confidence=0.5,
                        summary=f"Worker {n}",
                    ))
                    with lock:
                        master.merge(s)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        # 10 workers × 10 merges each = 100 tool results
        assert len(master.tool_results) == 100
        assert len(master.agent_assessments) == 100

    def test_coordinator_parallel_no_duplicate_assessments(self):
        """Parallel coordinator should not produce duplicate assessments."""
        issues = [
            _make_issue(f"TEX_RW_{i}", "TEX_READ_WRITE_ENABLED", "high",
                        f"Textures/{i}.png")
            for i in range(5)
        ]
        audit_result = _make_audit_result(issues)

        def agent_factory():
            return _make_fake_agent([{
                "action": "finish",
                "assessment": {
                    "issue_id": "TEX_RW_0",  # Will be overridden per group
                    "risk_level": "low",
                    "recommended_action": "auto_fix_candidate",
                    "confidence": 0.8,
                    "summary": "Done.",
                    "evidence_refs": [],
                    "needs_human_review": False,
                },
            }])

        coordinator = GroupCoordinator(
            agent_factory=agent_factory,
            tool_registry=_make_tool_registry(),
            audit_result=audit_result,
            max_workers=3,
            max_steps_per_group=12,
            trace_enabled=False,
        )

        state = RunState(run_id="nodup_test", project_root="/fake", platform="Android")

        # Each issue is its own group (different paths)
        groups = []
        for issue in issues:
            rep_data = {
                "issue_id": issue.issue_id,
                "rule_id": issue.rule_id,
                "severity": issue.severity,
                "asset_path": issue.asset_path,
                "title": issue.title,
                "message": issue.message,
                "evidence": {},
                "suggestion": "Fix",
                "auto_fixable": True,
                "path_category": "texture",
            }
            groups.append(((issue.rule_id, issue.severity, issue.asset_path),
                          rep_data, []))

        final_state = coordinator.dispatch(groups, state)

        # Should have exactly 5 assessments (one per issue)
        assert len(final_state.agent_assessments) == 5

        # No duplicate issue IDs in assessments
        assessment_ids = [a.issue_id for a in final_state.agent_assessments]
        assert len(assessment_ids) == len(set(assessment_ids)), (
            f"Duplicate assessments found: {assessment_ids}"
        )


# ═══════════════════════════════════════════════════════════════════
# TestTraceWorkerEvents
# ═══════════════════════════════════════════════════════════════════

class TestTraceWorkerEvents:
    """Tests for worker_started / worker_completed trace events."""

    def test_worker_events_in_trace(self, tmp_path):
        """Trace should include worker_started and worker_completed events."""
        writer = TraceWriter("run_w")
        writer.run_started("/fake", "Android")
        writer.worker_started(0, "TEX_RW/high/texture")
        writer.worker_completed(0, "TEX_RW/high/texture",
                                assessments=1, tool_calls=2, success=True)
        writer.run_completed(1, "completed", assessments_count=1, tool_calls=2)

        trace_path = tmp_path / "trace.jsonl"
        writer.save(str(trace_path))

        content = trace_path.read_text()
        assert "worker_started" in content
        assert "worker_completed" in content
        assert "TEX_RW/high/texture" in content

    def test_worker_events_include_worker_id(self, tmp_path):
        """Worker events should include worker_id."""
        import json

        writer = TraceWriter("run_wid")
        writer.run_started("/f", "A")
        writer.worker_started(3, "group/key")
        writer.worker_completed(3, "group/key")
        writer.run_completed(1, "completed")

        trace_path = tmp_path / "trace.jsonl"
        writer.save(str(trace_path))

        lines = trace_path.read_text().strip().split("\n")
        worker_started_events = [
            json.loads(line) for line in lines
            if json.loads(line)["event_type"] == "worker_started"
        ]
        assert len(worker_started_events) == 1
        assert worker_started_events[0]["payload"]["worker_id"] == 3
