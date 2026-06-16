"""CLI entry point for the Unity Static Asset Audit Agent."""

import argparse
import os
import sys

from unity_audit.application.audit_service import AuditService
from unity_audit.application.models import AuditRequest
from unity_audit.config import AuditConfig, load_config, merge_cli_with_config
from unity_audit.fix_planner import FixDecision
from unity_audit.report import (
    _dict_to_evidence_result,
    _dict_to_fix_decision,
    _dict_to_issue,
    generate_ci_annotations,
    generate_html_report,
    generate_json_reports,
    generate_markdown_report,
    get_git_changed_files,
    load_audit_cache,
    save_audit_cache,
)


def _run_deterministic_scan(
    project_root: str,
    output_dir: str,
    platform: str,
    config: AuditConfig,
    ci_format: str | None = None,
    incremental: bool = False,
    base_ref: str | None = None,
) -> tuple[int, AuditRequest, object]:
    """Run the deterministic scan pipeline and generate reports.

    Returns:
        Tuple of (exit_code, audit_request, audit_result).
    """
    # Resolve path filter for incremental mode
    path_filter = None
    if incremental:
        base = base_ref or "HEAD~1"
        changed = get_git_changed_files(project_root, base)
        if changed is None:
            print("  [WARN] Could not get git diff. Falling back to full scan.")
        elif not changed:
            print(f"  No assets changed since {base}. Reusing cached results.")
            # Load cache and return early
            cache = load_audit_cache(output_dir)
            if cache:
                print(f"  Loaded {len(cache.get('issues', []))} cached issues.")
                # We still generate reports from cache
                # Build minimal result from cache
            else:
                print("  No cache found. Running full scan to establish baseline.")
        else:
            print(f"  Incremental: {len(changed)} asset(s) changed since {base}")
            path_filter = changed

    request = AuditRequest(
        project_root=project_root,
        platform=platform,
        config={"rules": config.rules, "agent": config.agent},
        path_filter=path_filter,
    )

    print("Unity Static Asset Audit Agent v0.2.0")
    print(f"Project: {project_root}")
    print(f"Platform: {platform}")
    print(f"Output: {output_dir}")
    if incremental and path_filter:
        print(f"Mode: Incremental ({len(path_filter)} changed assets)")
    print()

    # Run deterministic pipeline
    print("Scanning project...")
    service = AuditService()
    result = service.run_scan(request)

    # Report scan errors
    for warning in result.warnings:
        print(f"  [WARN] {warning}")
    for error in result.errors:
        print(f"  [ERROR] {error}")

    if result.errors:
        print("Scan failed due to errors.")
        return 1, request, result

    if not result.assets:
        print("No recognizable Unity assets found.")
        # Write empty reports
        try:
            generate_json_reports(output_dir, [], [], [])
            generate_markdown_report(
                output_dir=output_dir,
                project_root=project_root,
                platform=platform,
                assets=[],
                issues=[],
                fix_decisions=[],
                evidence_map={},
                warnings=result.warnings,
                llm_used=False,
            )
            generate_html_report(
                output_dir=output_dir,
                project_root=project_root,
                platform=platform,
                assets=[],
                issues=[],
                fix_decisions=[],
                evidence_map={},
                warnings=result.warnings,
                llm_used=False,
            )
        except OSError:
            return 2, request, result
        return 0, request, result

    print(f"  Scanned assets: {len(result.assets)}")
    print(f"  Issues found: {len(result.issues)}")

    # Summary counts
    auto_count = sum(1 for d in result.fix_decisions if d.action == "auto_fix_candidate")
    manual_count = sum(1 for d in result.fix_decisions if d.action == "manual_confirm_required")
    nofix_count = sum(1 for d in result.fix_decisions if d.action == "do_not_fix")
    print(f"  Auto-fix: {auto_count}, Manual: {manual_count}, Do-not-fix: {nofix_count}")
    print()

    # Generate reports
    print("Generating reports...")
    try:
        # Merge with cache for incremental mode
        merged_issues = list(result.issues)
        merged_decisions = list(result.fix_decisions)
        merged_evidence = dict(result.evidence_map)

        if incremental:
            cache = load_audit_cache(output_dir)
            if cache:
                # Keep cached results for unchanged assets
                fresh_asset_paths = {i.asset_path for i in result.issues}
                for cached_issue in cache.get("issues", []):
                    if cached_issue["asset_path"] not in fresh_asset_paths:
                        merged_issues.append(_dict_to_issue(cached_issue))
                for cached_decision in cache.get("fix_decisions", []):
                    if cached_decision["asset_path"] not in fresh_asset_paths:
                        merged_decisions.append(_dict_to_fix_decision(cached_decision))
                for eid, ev_dict in cache.get("evidence", {}).items():
                    if eid not in merged_evidence:
                        merged_evidence[eid] = _dict_to_evidence_result(ev_dict)
                print(f"  Merged: {len(result.issues)} fresh + "
                      f"{len(merged_issues) - len(result.issues)} cached issues")

        # Save cache for next incremental scan
        save_audit_cache(
            output_dir, merged_issues, merged_decisions, merged_evidence,
        )

        generate_json_reports(output_dir, result.assets, merged_issues, merged_decisions)
        report_path = generate_markdown_report(
            output_dir=output_dir,
            project_root=project_root,
            platform=platform,
            assets=result.assets,
            issues=merged_issues,
            fix_decisions=merged_decisions,
            evidence_map=merged_evidence,
            warnings=result.warnings,
            llm_used=False,
        )
        generate_html_report(
            output_dir=output_dir,
            project_root=project_root,
            platform=platform,
            assets=result.assets,
            issues=merged_issues,
            fix_decisions=merged_decisions,
            evidence_map=merged_evidence,
            warnings=result.warnings,
            llm_used=False,
        )
        if ci_format:
            ci_path = generate_ci_annotations(
                output_dir=output_dir,
                issues=merged_issues,
                fix_decisions=merged_decisions,
                evidence_map=merged_evidence,
                ci_format=ci_format,
            )
            print(f"CI annotations written to: {ci_path}")
    except OSError as e:
        print(f"  [ERROR] Failed to write reports: {e}", file=sys.stderr)
        return 2, request, result

    elapsed = result.elapsed_seconds
    print(f"Report written to: {report_path}")
    print(f"Time elapsed: {elapsed:.2f}s")

    return 0, request, result


def _run_agent_mode(
    project_root: str,
    output_dir: str,
    platform: str,
    config: AuditConfig,
    resume_from: str | None = None,
    trace_enabled: bool = True,
    ci_format: str | None = None,
    incremental: bool = False,
    base_ref: str | None = None,
) -> tuple[int, AuditRequest, object, bool]:
    """Run the Agent-enhanced audit mode.

    Returns:
        Tuple of (exit_code, request, result, agent_used).
        agent_used is True if the agent actually contributed (not fallback).
    """
    # Resolve path filter for incremental mode
    path_filter = None
    if incremental:
        base = base_ref or "HEAD~1"
        changed = get_git_changed_files(project_root, base)
        if changed is None:
            print("  [WARN] Could not get git diff. Falling back to full scan.")
        elif not changed:
            print(f"  No assets changed since {base}. Reusing cached results.")
            cache = load_audit_cache(output_dir)
            if not cache:
                print("  No cache found. Running full scan to establish baseline.")
            # For agent mode, still need to run scan for changed files (none in this case)
        else:
            print(f"  Incremental: {len(changed)} asset(s) changed since {base}")
            path_filter = changed

    request = AuditRequest(
        project_root=project_root,
        platform=platform,
        config={"rules": config.rules, "agent": config.agent},
        path_filter=path_filter,
    )

    print("Unity Static Asset Audit Agent v0.2.0 [Agent Mode]")
    print(f"Project: {project_root}")
    print(f"Platform: {platform}")
    print(f"Output: {output_dir}")
    if incremental and path_filter:
        print(f"Mode: Incremental ({len(path_filter)} changed assets)")
    print()

    # Run deterministic pipeline first
    print("Running deterministic scan...")
    service = AuditService()
    result = service.run_scan(request)

    for warning in result.warnings:
        print(f"  [WARN] {warning}")
    for error in result.errors:
        print(f"  [ERROR] {error}")

    if result.errors:
        print("Scan failed due to errors.")
        return 1, request, result, False

    if not result.assets:
        print("No recognizable Unity assets found.")
        try:
            generate_json_reports(output_dir, [], [], [])
            generate_markdown_report(
                output_dir=output_dir, project_root=project_root,
                platform=platform, assets=[], issues=[], fix_decisions=[],
                evidence_map={}, warnings=result.warnings, llm_used=False,
            )
            generate_html_report(
                output_dir=output_dir, project_root=project_root,
                platform=platform, assets=[], issues=[], fix_decisions=[],
                evidence_map={}, warnings=result.warnings, llm_used=False,
            )
        except OSError:
            return 2, request, result, False
        return 0, request, result, False

    print(f"  Issues found: {len(result.issues)}")
    print()

    # Try Agent mode
    agent_used = False

    # Check if we should attempt agent
    agent_cfg = config.agent
    if not agent_cfg.get("enabled"):
        print("Agent mode not enabled in config. Skipping agent phase.")
    else:
        print("Starting Agent phase...")
        try:
            from unity_audit.agents.audit_agent import AuditAgent
            from unity_audit.agents.model_client import create_model_client
            from unity_audit.harness.runner import HarnessRunner
            from unity_audit.harness.state import RunState, RunStatus

            max_steps = agent_cfg.get("max_steps", 12)
            timeout = agent_cfg.get("timeout_seconds", 60)
            model_name = agent_cfg.get("model", "claude-sonnet-4-6")
            model_client = None

            try:
                model_client = create_model_client(
                    model_name,
                    api_key=None,  # Let factory resolve from env/.env
                    timeout=timeout,
                )
            except Exception as e:
                print(f"  No API key configured: {e}")
                print("  Falling back to deterministic results.")

            final_state = None

            if model_client is not None:

                agent = AuditAgent(model_client=model_client)

                runner = HarnessRunner(
                    agent=agent,
                    audit_result=result,
                    max_steps=max_steps,
                    trace_enabled=trace_enabled,
                )

                # Build initial state
                pending_ids = [i.issue_id for i in result.issues]
                state = RunState(
                    run_id=RunState.generate_run_id(),
                    project_root=project_root,
                    platform=platform,
                    pending_issue_ids=pending_ids,
                    max_steps=max_steps,
                )

                if resume_from:
                    print(f"  Resuming from: {resume_from}")
                    state = RunState.load(resume_from)

                # Run the harness
                final_state = runner.run(state, result)

                # Save run artifacts
                run_path = os.path.join(output_dir, "run.json")
                final_state.save(run_path)

                if trace_enabled:
                    trace_path = os.path.join(output_dir, "trace.jsonl")
                    runner.trace_writer.save(trace_path)

                # Save assessments
                assessments_path = os.path.join(output_dir, "agent_assessments.json")
                final_state.save_assessments(assessments_path)

                if final_state.status == RunStatus.COMPLETED:
                    agent_used = True
                    total = len(result.issues)
                    assessed = len(final_state.agent_assessments)
                    if assessed < total:
                        print(f"  Agent completed: {assessed} assessments "
                              f"(dedup: {total} issues → {assessed} unique groups)")
                    else:
                        print(f"  Agent completed: {assessed} assessments")
                elif final_state.agent_assessments:
                    # Partial success — some assessments produced before step limit
                    agent_used = True
                    total = len(result.issues)
                    assessed = len(final_state.agent_assessments)
                    print(f"  Agent partial: {assessed} assessments "
                          f"({total} issues → {assessed} groups, "
                          f"step limit reached)")
                else:
                    print(f"  Agent status: {final_state.status}, using deterministic results")

                # Display token/time statistics
                if model_client is not None and model_client.call_count > 0:
                    print(f"  API calls: {model_client.call_count}")
                    usage = model_client.total_usage
                    if usage.total_tokens > 0:
                        print(f"  Tokens: {usage.total_tokens} total "
                              f"({usage.prompt_tokens} prompt + "
                              f"{usage.completion_tokens} completion)")

        except ImportError as e:
            print(f"  Agent module not available: {e}")
            print("  Falling back to deterministic results.")
        except Exception as e:
            print(f"  Agent error: {e}")
            print("  Falling back to deterministic results.")

    # Generate reports
    print()
    print("Generating reports...")
    try:
        # Merge with cache for incremental mode
        merged_issues = list(result.issues)
        merged_decisions = list(result.fix_decisions)
        merged_evidence = dict(result.evidence_map)

        if incremental:
            cache = load_audit_cache(output_dir)
            if cache:
                fresh_asset_paths = {i.asset_path for i in result.issues}
                for cached_issue in cache.get("issues", []):
                    if cached_issue["asset_path"] not in fresh_asset_paths:
                        merged_issues.append(_dict_to_issue(cached_issue))
                for cached_decision in cache.get("fix_decisions", []):
                    if cached_decision["asset_path"] not in fresh_asset_paths:
                        merged_decisions.append(_dict_to_fix_decision(cached_decision))
                for eid, ev_dict in cache.get("evidence", {}).items():
                    if eid not in merged_evidence:
                        merged_evidence[eid] = _dict_to_evidence_result(ev_dict)
                print(f"  Merged: {len(result.issues)} fresh + "
                      f"{len(merged_issues) - len(result.issues)} cached issues")

        # Merge agent assessments into fix_decisions if agent ran
        if agent_used and final_state is not None and final_state.agent_assessments:
            assessment_by_id = {
                a.issue_id: a for a in final_state.agent_assessments
            }
            agent_merged = []
            for d in merged_decisions:
                agent_a = assessment_by_id.get(d.issue_id)
                if agent_a:
                    agent_merged.append(FixDecision(
                        issue_id=d.issue_id,
                        rule_id=d.rule_id,
                        asset_path=d.asset_path,
                        severity=d.severity,
                        action=agent_a.recommended_action,
                        risk_level=agent_a.risk_level,
                        reason=agent_a.summary,
                        suggestion=d.suggestion,
                    ))
                else:
                    agent_merged.append(d)
            merged_decisions = agent_merged
            print(f"  Agent assessments merged: "
                  f"{len(assessment_by_id)} issues updated")

        # Save cache for next incremental scan
        save_audit_cache(
            output_dir, merged_issues, merged_decisions, merged_evidence,
        )

        generate_json_reports(output_dir, result.assets, merged_issues, merged_decisions)
        report_path = generate_markdown_report(
            output_dir=output_dir,
            project_root=project_root,
            platform=platform,
            assets=result.assets,
            issues=merged_issues,
            fix_decisions=merged_decisions,
            evidence_map=merged_evidence,
            warnings=result.warnings,
            llm_used=agent_used,
        )
        generate_html_report(
            output_dir=output_dir,
            project_root=project_root,
            platform=platform,
            assets=result.assets,
            issues=merged_issues,
            fix_decisions=merged_decisions,
            evidence_map=merged_evidence,
            warnings=result.warnings,
            llm_used=agent_used,
        )
        if ci_format:
            ci_path = generate_ci_annotations(
                output_dir=output_dir,
                issues=merged_issues,
                fix_decisions=merged_decisions,
                evidence_map=merged_evidence,
                ci_format=ci_format,
            )
            print(f"CI annotations written to: {ci_path}")
    except OSError as e:
        print(f"  [ERROR] Failed to write reports: {e}", file=sys.stderr)
        return 2, request, result, agent_used

    elapsed = result.elapsed_seconds
    print(f"Report written to: {report_path}")
    print(f"Agent: {'Enhanced' if agent_used else 'Fallback (deterministic only)'}")
    print(f"Time elapsed: {elapsed:.2f}s")

    return 0, request, result, agent_used


def cmd_scan(args: argparse.Namespace) -> int:
    """Run the scan command (deterministic mode).

    Returns:
        Exit code: 0=success, 1=scan/config error, 2=output error.
    """
    project_root = os.path.abspath(args.project)
    output_dir = os.path.abspath(args.output) if args.output else os.path.join(os.getcwd(), "outputs")

    # Load config
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    # Merge CLI args
    config = merge_cli_with_config(
        config,
        cli_platform=args.platform,
        cli_agent=args.agent,
        cli_model=getattr(args, "model", None),
        cli_max_steps=getattr(args, "max_agent_steps", None),
    )

    platform = config.platform or args.platform or "Unknown"

    # Show config warnings
    for w in config.warnings:
        print(f"  [CONFIG WARN] {w}")

    # Determine mode: agent or deterministic
    ci_format = getattr(args, "ci", None)
    incremental = getattr(args, "incremental", False)
    base_ref = getattr(args, "base", None)
    if args.agent:
        trace_enabled = not getattr(args, "no_trace", False)
        exit_code, request, result, agent_used = _run_agent_mode(
            project_root=project_root,
            output_dir=output_dir,
            platform=platform,
            config=config,
            resume_from=getattr(args, "resume", None),
            trace_enabled=trace_enabled,
            ci_format=ci_format,
            incremental=incremental,
            base_ref=base_ref,
        )
    else:
        exit_code, request, result = _run_deterministic_scan(
            project_root=project_root,
            output_dir=output_dir,
            platform=platform,
            config=config,
            ci_format=ci_format,
            incremental=incremental,
            base_ref=base_ref,
        )

    return exit_code


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="unity-audit",
        description="Unity Static Asset Audit Agent - Scan Unity projects for asset issues",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # scan command (kept for backward compatibility)
    _build_scan_parser(subparsers)

    args = parser.parse_args()

    if args.command == "scan":
        return cmd_scan(args)
    elif args.command is None:
        parser.print_help()
        return 0
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


def _build_scan_parser(subparsers):
    """Build the 'scan' subparser with all arguments."""
    scan_parser = subparsers.add_parser("scan", help="Scan a Unity project")
    scan_parser.add_argument(
        "project",
        help="Path to the Unity project root",
    )
    scan_parser.add_argument(
        "--platform",
        default=None,
        help="Target platform (e.g., Android, iOS, WebGL)",
    )
    scan_parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output directory for reports (default: ./outputs)",
    )
    scan_parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to YAML config file",
    )
    scan_parser.add_argument(
        "--agent",
        action="store_true",
        default=False,
        help="Enable Agent mode (single-agent harness with LLM)",
    )
    scan_parser.add_argument(
        "--model",
        default=None,
        help="Model name for Agent mode (e.g., claude-sonnet-4-6)",
    )
    scan_parser.add_argument(
        "--max-agent-steps",
        type=int,
        default=None,
        help="Maximum agent steps (default: 12)",
    )
    scan_parser.add_argument(
        "--resume",
        default=None,
        help="Resume from a checkpoint run.json",
    )
    scan_parser.add_argument(
        "--no-trace",
        action="store_true",
        default=False,
        help="Disable trace.jsonl output",
    )
    scan_parser.add_argument(
        "--ci",
        default=None,
        choices=["github", "gitlab"],
        help="Generate CI annotation output (github or gitlab format)",
    )
    scan_parser.add_argument(
        "--incremental",
        action="store_true",
        default=False,
        help="Only scan assets changed since last commit (uses git diff)",
    )
    scan_parser.add_argument(
        "--base",
        default=None,
        help="Base ref for git diff in incremental mode (default: HEAD~1)",
    )
    # Deprecated --llm flag: warn but accept for compatibility
    scan_parser.add_argument(
        "--llm",
        default=None,
        nargs="?",
        const="deprecated",
        help="[DEPRECATED] Use --agent instead",
    )


if __name__ == "__main__":
    sys.exit(main())
