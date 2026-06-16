"""Report Generator - Generate JSON and Markdown reports."""

import json
import os
from collections import Counter

from unity_audit import SCHEMA_VERSION
from unity_audit.evidence import EvidenceResult
from unity_audit.fix_planner import FixDecision
from unity_audit.rules.engine import Issue
from unity_audit.scanner import AssetInfo


def _add_schema_version(data: dict) -> dict:
    """Add schema_version to a JSON-serializable dict."""
    if isinstance(data, list):
        return {"schema_version": SCHEMA_VERSION, "items": data}
    return {"schema_version": SCHEMA_VERSION, **data}


def generate_json_reports(
    output_dir: str,
    assets: list[AssetInfo],
    issues: list[Issue],
    fix_decisions: list[FixDecision],
):
    """Generate assets.json, issues.json, and fix_decisions.json.

    Args:
        output_dir: Directory to write JSON files to.
        assets: Scanned asset list.
        issues: All issues found.
        fix_decisions: Fix decisions for each issue.
    """
    os.makedirs(output_dir, exist_ok=True)

    # assets.json
    assets_data = [
        {
            "asset_path": a.asset_path,
            "asset_type": a.asset_type,
            "extension": a.extension,
            "file_size": a.file_size,
            "meta_path": a.meta_path,
        }
        for a in assets
    ]
    with open(os.path.join(output_dir, "assets.json"), "w", encoding="utf-8") as f:
        json.dump(_add_schema_version(assets_data), f, ensure_ascii=False, indent=2)

    # issues.json
    issues_data = [
        {
            "issue_id": i.issue_id,
            "rule_id": i.rule_id,
            "severity": i.severity,
            "asset_path": i.asset_path,
            "title": i.title,
            "message": i.message,
            "evidence": i.evidence,
            "suggestion": i.suggestion,
            "auto_fixable": i.auto_fixable,
        }
        for i in issues
    ]
    with open(os.path.join(output_dir, "issues.json"), "w", encoding="utf-8") as f:
        json.dump(_add_schema_version(issues_data), f, ensure_ascii=False, indent=2)

    # fix_decisions.json
    decisions_data = [
        {
            "issue_id": d.issue_id,
            "rule_id": d.rule_id,
            "asset_path": d.asset_path,
            "severity": d.severity,
            "action": d.action,
            "risk_level": d.risk_level,
            "reason": d.reason,
            "suggestion": d.suggestion,
        }
        for d in fix_decisions
    ]
    with open(os.path.join(output_dir, "fix_decisions.json"), "w", encoding="utf-8") as f:
        json.dump(_add_schema_version(decisions_data), f, ensure_ascii=False, indent=2)


def generate_markdown_report(
    output_dir: str,
    project_root: str,
    platform: str,
    assets: list[AssetInfo],
    issues: list[Issue],
    fix_decisions: list[FixDecision],
    evidence_map: dict[str, EvidenceResult],
    warnings: list[str],
    llm_used: bool = False,
) -> str:
    """Generate report.md — grouped by rule for readability at scale.

    Instead of listing every issue individually (unreadable when there are
    hundreds of the same type), issues are grouped by rule_id with per-rule
    summaries, representative examples, and compact asset lists.
    """
    os.makedirs(output_dir, exist_ok=True)

    # ── Pre-compute aggregates ──────────────────────────────────────────
    severity_counts = Counter(i.severity for i in issues)
    Counter(i.rule_id for i in issues)
    action_counts = Counter(d.action for d in fix_decisions)
    type_counts = Counter(a.asset_type for a in assets)
    decisions_by_issue = {d.issue_id: d for d in fix_decisions}

    # Group issues by rule_id
    from collections import defaultdict
    issues_by_rule: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        issues_by_rule[i.rule_id].append(i)

    # ── Rule-level descriptions (generic, not per-asset) ─────────────────
    RULE_DESCRIPTIONS = {
        "TEX_UI_MIPMAP_ENABLED": {
            "what": "UI textures have mipmaps enabled",
            "why": "Mipmaps increase texture memory by ~33% but provide no visual benefit for UI elements that render at native resolution.",
            "fix": "Disable mipmap generation for UI textures in the Texture Importer.",
        },
        "TEX_READ_WRITE_ENABLED": {
            "what": "Texture has Read/Write enabled",
            "why": "Read/Write doubles memory usage because Unity keeps a CPU-accessible copy of the texture data in addition to the GPU copy.",
            "fix": "If the texture is not modified at runtime via GetPixels/SetPixels, disable Read/Write in the Texture Importer.",
        },
        "TEX_UI_MAX_SIZE_TOO_LARGE": {
            "what": "UI texture max size exceeds platform-appropriate limit",
            "why": "Oversized UI textures waste memory without visual quality gains on the target device.",
            "fix": "Reduce the Max Size in Texture Importer to match the largest on-screen usage.",
        },
        "TEX_NPOT_DETECTED": {
            "what": "Non-power-of-two (NPOT) texture dimensions",
            "why": "NPOT textures may cause compatibility issues on some platforms, cannot use DXT/ETC compression, and may be resampled at upload time. Dimensions vary per texture.",
            "fix": "Evaluate whether NPOT dimensions are intentional. If possible, adjust to power-of-two sizes for better compression and compatibility.",
        },
        "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD": {
            "what": "Long audio clip uses Decompress On Load",
            "why": "Decompress On Load for long clips causes high memory usage and long loading times.",
            "fix": "Switch to Streaming or Compressed In Memory for long audio clips.",
        },
        "AUD_STEREO_SFX": {
            "what": "SFX audio is stereo",
            "why": "Stereo SFX uses 2× the memory of mono with minimal spatial benefit. Short sound effects are typically fine in mono.",
            "fix": "Enable Force To Mono in the Audio Importer unless stereo is essential.",
        },
        "PREFAB_MISSING_SCRIPT": {
            "what": "Missing script references in prefab/scene",
            "why": "A MonoBehaviour script referenced in the prefab/scene cannot be found. This causes runtime errors and data loss.",
            "fix": "Locate or recreate the missing script, or remove the broken component from the prefab/scene.",
        },
        "UI_TOO_MANY_GRAPHIC_RAYCASTERS": {
            "what": "Multiple GraphicRaycaster components in the scene",
            "why": "Multiple GraphicRaycaster components consume extra CPU per frame for unnecessary raycast rebuilds.",
            "fix": "Remove duplicate GraphicRaycaster components, keeping only one per Canvas hierarchy.",
        },
    }

    # ── Helper: common path prefix for compact display ──────────────────
    def _common_prefix(paths: list[str], min_segments: int = 1) -> str:
        """Find the longest common directory prefix from a list of paths."""
        if not paths:
            return ""
        parts_list = [p.replace("\\", "/").split("/") for p in paths]
        prefix_parts = []
        for segs in zip(*parts_list, strict=False):
            if len(set(segs)) == 1:
                prefix_parts.append(segs[0])
            else:
                break
        if len(prefix_parts) > min_segments:
            return "/".join(prefix_parts) + "/"
        return ""

    def _group_by_dir(paths: list[str]) -> list[tuple[str, int]]:
        """Group asset paths by 1st-level directory under common prefix."""
        prefix = _common_prefix(paths)
        groups: dict[str, int] = defaultdict(int)
        for p in paths:
            relative = p[len(prefix):] if prefix else p
            top_dir = relative.split("/")[0] if "/" in relative else "(root)"
            groups[top_dir] += 1
        # Sort by count descending
        return sorted(groups.items(), key=lambda x: (-x[1], x[0]))

    # ── Build report ────────────────────────────────────────────────────
    lines = []
    lines.append("# Unity Static Asset Audit Report")
    lines.append("")
    lines.append("**Generated by:** Unity Static Asset Audit Agent v0.2.0  ")
    lines.append(f"**LLM Enhanced:** {'Yes' if llm_used else 'No'}  ")
    lines.append("")

    # ── 1. Summary ──────────────────────────────────────────────────────
    lines.append("## 1. Summary")
    lines.append("")
    lines.append("| Item | Value |")
    lines.append("|------|-------|")
    lines.append(f"| Project Root | `{project_root}` |")
    lines.append(f"| Platform | {platform} |")
    lines.append(f"| Scanned Assets | {len(assets)} |")
    for atype, count in sorted(type_counts.items()):
        lines.append(f"| — {atype} | {count} |")
    lines.append(f"| **Issues Found** | **{len(issues)}** |")
    for sev in ("critical", "high", "medium", "low"):
        count = severity_counts.get(sev, 0)
        if count:
            lines.append(f"| — {sev.capitalize()} | {count} |")
    if warnings:
        lines.append(f"| Warnings | {len(warnings)} |")
    lines.append("")

    # ── 2. Issues by Rule ───────────────────────────────────────────────
    lines.append("## 2. Issues by Rule")
    lines.append("")
    for rule_id in sorted(issues_by_rule.keys()):
        rule_issues = issues_by_rule[rule_id]
        count = len(rule_issues)
        # Use generic rule description when available, else first issue
        sev = rule_issues[0].severity
        desc = RULE_DESCRIPTIONS.get(rule_id, {})
        what = desc.get("what", rule_issues[0].title)
        why = desc.get("why", rule_issues[0].message)
        fix = desc.get("fix", rule_issues[0].suggestion)
        evidence = rule_issues[0].evidence

        # Decision summary for this rule
        rule_decisions = [decisions_by_issue[i.issue_id] for i in rule_issues
                          if i.issue_id in decisions_by_issue]
        rule_actions = Counter(d.action for d in rule_decisions)

        lines.append(f"### {rule_id} ({count} issues, severity: {sev})")
        lines.append("")
        lines.append(f"**What:** {what}  ")
        lines.append(f"**Why:** {why}  ")
        lines.append(f"**Fix:** {fix}  ")
        lines.append("")

        # Decision summary
        lines.append("| Recommendation | Count |")
        lines.append("|----------------|-------|")
        for action in ("auto_fix_candidate", "manual_confirm_required", "do_not_fix"):
            cnt = rule_actions.get(action, 0)
            if cnt:
                label = action.replace("_", " ")
                lines.append(f"| {label} | {cnt} |")
        lines.append("")

        # Evidence (once, since it's the same for all issues of this rule)
        if evidence:
            lines.append(f"**Evidence fields:** {', '.join(f'`{k}`' for k in evidence.keys())}")
            lines.append("")

        # Compact asset listing — group by top-level directory
        asset_paths = [i.asset_path for i in rule_issues]
        common = _common_prefix(asset_paths)
        if common:
            lines.append(f"**Common path prefix:** `{common}`")
            lines.append("")

        dir_groups = _group_by_dir(asset_paths)
        if len(dir_groups) <= 15:
            # Show per-directory groupings
            lines.append("**Affected directories:**")
            lines.append("")
            for dirname, cnt in dir_groups:
                lines.append(f"- `{dirname}/` — {cnt} asset(s)")
            lines.append("")
        else:
            # Too many groups, just list sample paths
            lines.append(f"**Affected assets ({count} total):**")
            lines.append("")
            lines.append("<details>")
            lines.append("<summary>Click to expand asset list</summary>")
            lines.append("")
            for p in sorted(asset_paths)[:50]:
                lines.append(f"- `{p}`")
            if count > 50:
                lines.append(f"- ... and {count - 50} more")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        # Sample fix decisions (up to 3)
        sample_decisions = rule_decisions[:3]
        if sample_decisions:
            lines.append("**Example fix decisions:**")
            lines.append("")
            for d in sample_decisions:
                lines.append(f"- `{d.asset_path}` → **{d.action.replace('_', ' ')}** "
                           f"(risk: {d.risk_level})")
                if d.reason:
                    lines.append(f"  > {d.reason[:120]}")
            lines.append("")

    # ── 3. Fix Decision Summary ─────────────────────────────────────────
    lines.append("## 3. Fix Decision Summary")
    lines.append("")
    lines.append("| Decision | Count |")
    lines.append("|----------|-------|")
    for action in ("auto_fix_candidate", "manual_confirm_required", "do_not_fix"):
        cnt = action_counts.get(action, 0)
        if cnt:
            lines.append(f"| {action.replace('_', ' ')} | {cnt} |")
    lines.append("")

    # ── 4. High Risk Evidence ───────────────────────────────────────────
    lines.append("## 4. High Risk Evidence (Code References)")
    lines.append("")
    high_risk = [(i, evidence_map.get(i.issue_id))
                 for i in issues if i.severity in ("critical", "high")]
    high_with_code = [(i, e) for i, e in high_risk
                      if e and e.code_search_result]

    if high_with_code:
        # Group by rule
        high_by_rule: dict[str, list] = defaultdict(list)
        for issue, ev in high_with_code:
            high_by_rule[issue.rule_id].append((issue, ev))

        for rule_id, items in high_by_rule.items():
            lines.append(f"### {rule_id} ({len(items)} issues with code matches)")
            lines.append("")
            # Show unique code files across all matches
            all_files: dict[str, int] = defaultdict(int)
            for _, ev in items:
                for m in ev.code_search_result[:5]:
                    fname = m.get("file", "")
                    if fname:
                        all_files[fname] += 1
            if all_files:
                lines.append("**Referenced code files:**")
                for fname, refs in sorted(all_files.items(), key=lambda x: -x[1]):
                    lines.append(f"- `{fname}` ({refs} reference(s))")
                lines.append("")
            # Representative example
            if items:
                iex, ev_ex = items[0]
                lines.append(f"**Example:** `{iex.asset_path}`")
                lines.append(f"- Context: {ev_ex.context_summary}")
                lines.append(f"- Risk: {ev_ex.risk_hint}")
                lines.append(f"- Needs manual confirm: {ev_ex.need_manual_confirm}")
                lines.append("")
    else:
        lines.append("*No high-risk issues with code evidence found.*")
        lines.append("")

    # ── 5. Warnings ─────────────────────────────────────────────────────
    if warnings:
        lines.append("## 5. Scan Warnings")
        lines.append("")
        for w in warnings[:20]:
            lines.append(f"- {w}")
        if len(warnings) > 20:
            lines.append(f"- ... and {len(warnings) - 20} more warnings")
        lines.append("")

    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


# ═══════════════════════════════════════════════════════════════════════════════
# HTML Interactive Report
# ═══════════════════════════════════════════════════════════════════════════════

_HTML_CSS = r"""
    :root {
        --bg: #f8f9fa;
        --card-bg: #ffffff;
        --text: #212529;
        --text-secondary: #6c757d;
        --border: #dee2e6;
        --accent: #4361ee;
        --accent-hover: #3a56d4;
        --severity-critical: #dc3545;
        --severity-high: #fd7e14;
        --severity-medium: #ffc107;
        --severity-low: #0d6efd;
        --action-auto: #198754;
        --action-manual: #fd7e14;
        --action-nofix: #6c757d;
        --radius: 6px;
        --shadow: 0 1px 3px rgba(0,0,0,.08);
    }
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Oxygen,Ubuntu,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:0}
    .container{max-width:1320px;margin:0 auto;padding:16px 24px}

    /* Header */
    .report-header{background:var(--card-bg);border-bottom:1px solid var(--border);padding:24px 0;box-shadow:var(--shadow)}
    .report-header h1{font-size:1.5rem;font-weight:700;margin-bottom:4px}
    .report-header .meta{color:var(--text-secondary);font-size:.875rem}
    .llm-badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:.75rem;font-weight:600}
    .llm-yes{background:#d1fae5;color:#065f46}
    .llm-no{background:#e5e7eb;color:#4b5563}

    /* Summary cards */
    .summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:20px 0}
    .summary-card{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);padding:16px;text-align:center;box-shadow:var(--shadow)}
    .summary-card .num{font-size:2rem;font-weight:700;line-height:1.2}
    .summary-card .label{font-size:.8rem;color:var(--text-secondary);margin-top:4px}
    .num.critical{color:var(--severity-critical)}
    .num.high{color:var(--severity-high)}
    .num.medium{color:var(--severity-medium)}
    .num.low{color:var(--severity-low)}

    /* Filter bar */
    .filter-bar{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:20px;box-shadow:var(--shadow);position:sticky;top:8px;z-index:10}
    .filter-bar .filter-row{display:flex;flex-wrap:wrap;gap:16px;align-items:center}
    .filter-group{display:flex;align-items:center;gap:6px}
    .filter-group span.label{font-size:.8rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.5px;margin-right:4px}
    .filter-group label{display:inline-flex;align-items:center;gap:3px;font-size:.82rem;cursor:pointer;padding:3px 8px;border-radius:4px;border:1px solid transparent;user-select:none}
    .filter-group label:hover{background:#f1f3f5}
    .filter-group input[type=checkbox]{accent-color:var(--accent)}
    #search-input{padding:6px 12px;border:1px solid var(--border);border-radius:var(--radius);font-size:.85rem;width:240px;outline:none}
    #search-input:focus{border-color:var(--accent);box-shadow:0 0 0 2px rgba(67,97,238,.15)}
    .filter-stats{font-size:.8rem;color:var(--text-secondary);margin-top:8px}

    /* Severity/action badges */
    .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.75rem;font-weight:600;white-space:nowrap}
    .badge-critical{background:#fecaca;color:#991b1b}
    .badge-high{background:#fed7aa;color:#9a3412}
    .badge-medium{background:#fef3c7;color:#92400e}
    .badge-low{background:#dbeafe;color:#1e40af}
    .badge-auto{background:#d1fae5;color:#065f46}
    .badge-manual{background:#fed7aa;color:#9a3412}
    .badge-nofix{background:#e5e7eb;color:#4b5563}

    /* Rule sections */
    .rule-section{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:20px;box-shadow:var(--shadow);overflow:hidden}
    .rule-header{cursor:pointer;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;user-select:none;transition:background .15s}
    .rule-header:hover{background:#f8f9fa}
    .rule-header h2{font-size:1.1rem;font-weight:600;margin:0;display:flex;align-items:center;gap:8px}
    .rule-header .arrow{transition:transform .2s;font-size:.7rem;color:var(--text-secondary)}
    .rule-header.collapsed .arrow{transform:rotate(-90deg)}
    .rule-body{padding:0 20px 16px}
    .rule-body.collapsed{display:none}
    .rule-desc{padding:0 20px 16px;font-size:.85rem;color:var(--text-secondary);border-bottom:1px solid var(--border);margin-bottom:0}
    .rule-desc p{margin-bottom:4px}

    /* Table */
    table{width:100%;border-collapse:collapse;font-size:.85rem}
    thead th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);font-weight:600;color:var(--text-secondary);font-size:.78rem;text-transform:uppercase;letter-spacing:.4px;position:sticky;top:0;background:var(--card-bg)}
    tbody td{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
    tbody tr.issue-row{cursor:pointer;transition:background .1s}
    tbody tr.issue-row:hover{background:#f8f9fa}
    tbody tr.issue-row.hidden{display:none}
    .detail-row{background:#f8f9fa}
    .detail-row.hidden{display:none}
    .detail-row td{padding:12px 16px}
    .detail-content{font-size:.82rem}
    .detail-content dl{display:grid;grid-template-columns:120px 1fr;gap:4px 12px}
    .detail-content dt{font-weight:600;color:var(--text-secondary)}
    .detail-content dd{margin:0}
    .detail-content pre{background:#e9ecef;padding:8px 12px;border-radius:4px;font-size:.78rem;overflow-x:auto;margin-top:4px;max-height:200px;overflow-y:auto}
    .code-ref{margin-top:8px}
    .code-ref summary{font-weight:600;cursor:pointer;color:var(--accent)}
    .code-ref ul{margin:6px 0 0 16px}

    /* Expand/collapse buttons */
    .btn-row{display:flex;gap:8px;margin-bottom:12px}
    .btn{font-size:.78rem;padding:5px 14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card-bg);cursor:pointer;transition:all .15s}
    .btn:hover{background:#f1f3f5;border-color:#adb5bd}

    /* Warnings */
    .warnings-section{margin-top:20px}
    .warnings-section summary{font-weight:600;cursor:pointer;margin-bottom:8px}
    .warnings-section ul{margin-left:20px;font-size:.85rem;color:var(--text-secondary)}

    /* Path cell */
    .path-cell{font-family:"SF Mono","Fira Code","Cascadia Code",monospace;font-size:.8rem;word-break:break-all}
    .expand-icon{font-size:.7rem;color:var(--text-secondary);transition:transform .15s}
    .expand-icon.open{transform:rotate(90deg)}

    @media(max-width:768px){
        .container{padding:8px 12px}
        .filter-bar .filter-row{flex-direction:column;align-items:flex-start}
        #search-input{width:100%}
        .summary-grid{grid-template-columns:repeat(2,1fr)}
    }
"""


def generate_html_report(
    output_dir: str,
    project_root: str,
    platform: str,
    assets: list["AssetInfo"],
    issues: list["Issue"],
    fix_decisions: list["FixDecision"],
    evidence_map: dict[str, "EvidenceResult"],
    warnings: list[str],
    llm_used: bool = False,
) -> str:
    """Generate report.html — interactive single-file HTML report.

    Features: collapsible rule sections, severity/action filters,
    asset path search, click-to-expand evidence details, live counters.
    """
    import json as _json
    from collections import Counter, defaultdict

    os.makedirs(output_dir, exist_ok=True)

    # ── Aggregates ─────────────────────────────────────────────────────────
    severity_counts = Counter(i.severity for i in issues)
    action_counts = Counter(d.action for d in fix_decisions)
    type_counts = Counter(a.asset_type for a in assets)
    decisions_by_issue = {d.issue_id: d for d in fix_decisions}
    evidence_by_issue = evidence_map  # already keyed by issue_id

    issues_by_rule: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        issues_by_rule[i.rule_id].append(i)

    # Rule descriptions (same as markdown)
    RULE_DESCRIPTIONS = {
        "TEX_UI_MIPMAP_ENABLED": {
            "what": "UI textures have mipmaps enabled",
            "why": "Mipmaps increase texture memory by ~33% but provide no visual benefit for UI elements that render at native resolution.",
            "fix": "Disable mipmap generation for UI textures in the Texture Importer.",
        },
        "TEX_READ_WRITE_ENABLED": {
            "what": "Texture has Read/Write enabled",
            "why": "Read/Write doubles memory usage because Unity keeps a CPU-accessible copy of the texture data in addition to the GPU copy.",
            "fix": "If the texture is not modified at runtime via GetPixels/SetPixels, disable Read/Write in the Texture Importer.",
        },
        "TEX_UI_MAX_SIZE_TOO_LARGE": {
            "what": "UI texture max size exceeds platform-appropriate limit",
            "why": "Oversized UI textures waste memory without visual quality gains on the target device.",
            "fix": "Reduce the Max Size in Texture Importer to match the largest on-screen usage.",
        },
        "TEX_NPOT_DETECTED": {
            "what": "Non-power-of-two (NPOT) texture dimensions",
            "why": "NPOT textures may cause compatibility issues on some platforms, cannot use DXT/ETC compression, and may be resampled at upload time. Dimensions vary per texture.",
            "fix": "Evaluate whether NPOT dimensions are intentional. If possible, adjust to power-of-two sizes for better compression and compatibility.",
        },
        "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD": {
            "what": "Long audio clip uses Decompress On Load",
            "why": "Decompress On Load for long clips causes high memory usage and long loading times.",
            "fix": "Switch to Streaming or Compressed In Memory for long audio clips.",
        },
        "AUD_STEREO_SFX": {
            "what": "SFX audio is stereo",
            "why": "Stereo SFX uses 2× the memory of mono with minimal spatial benefit. Short sound effects are typically fine in mono.",
            "fix": "Enable Force To Mono in the Audio Importer unless stereo is essential.",
        },
        "PREFAB_MISSING_SCRIPT": {
            "what": "Missing script references in prefab/scene",
            "why": "A MonoBehaviour script referenced in the prefab/scene cannot be found. This causes runtime errors and data loss.",
            "fix": "Locate or recreate the missing script, or remove the broken component from the prefab/scene.",
        },
        "UI_TOO_MANY_GRAPHIC_RAYCASTERS": {
            "what": "Multiple GraphicRaycaster components in the scene",
            "why": "Multiple GraphicRaycaster components consume extra CPU per frame for unnecessary raycast rebuilds.",
            "fix": "Remove duplicate GraphicRaycaster components, keeping only one per Canvas hierarchy.",
        },
    }

    # ── Build embedded data ────────────────────────────────────────────────
    issues_json = _json.dumps([
        {
            "issue_id": i.issue_id,
            "rule_id": i.rule_id,
            "severity": i.severity,
            "asset_path": i.asset_path,
            "title": i.title,
            "message": i.message,
            "evidence": i.evidence,
            "suggestion": i.suggestion,
            "auto_fixable": i.auto_fixable,
        }
        for i in issues
    ], ensure_ascii=False)

    decisions_json = _json.dumps([
        {
            "issue_id": d.issue_id,
            "rule_id": d.rule_id,
            "asset_path": d.asset_path,
            "severity": d.severity,
            "action": d.action,
            "risk_level": d.risk_level,
            "reason": d.reason,
            "suggestion": d.suggestion,
        }
        for d in fix_decisions
    ], ensure_ascii=False)

    evidence_json = _json.dumps({
        eid: {
            "context_summary": ev.context_summary,
            "risk_hint": ev.risk_hint,
            "need_manual_confirm": ev.need_manual_confirm,
            "association_level": ev.association_level,
            "code_evidence": [
                {
                    "file": ce.file,
                    "line": ce.line,
                    "content": ce.content,
                    "api": ce.api,
                    "association_type": ce.association_type,
                }
                for ce in ev.code_evidence
            ],
        }
        for eid, ev in evidence_by_issue.items()
    }, ensure_ascii=False)

    meta_json = _json.dumps({
        "project_root": project_root,
        "platform": platform,
        "llm_used": llm_used,
        "total_assets": len(assets),
        "total_issues": len(issues),
        "total_warnings": len(warnings),
        "severity_counts": dict(severity_counts),
        "action_counts": dict(action_counts),
        "type_counts": dict(type_counts),
    }, ensure_ascii=False)

    # ── Build HTML sections ────────────────────────────────────────────────
    parts = []

    # Helper: severity badge
    def _sev_badge(sev: str) -> str:
        return f'<span class="badge badge-{sev}">{sev}</span>'

    # Helper: action badge
    def _act_badge(action: str) -> str:
        label = action.replace("_", " ")
        return f'<span class="badge badge-{_action_badge_class(action)}">{label}</span>'

    def _action_badge_class(action: str) -> str:
        if action == "auto_fix_candidate":
            return "auto"
        elif action == "manual_confirm_required":
            return "manual"
        return "nofix"

    # Escape for HTML text content
    def _esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    # ── Build HTML ─────────────────────────────────────────────────────────
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="UTF-8">')
    parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    parts.append("<title>Unity Static Asset Audit Report</title>")
    parts.append(f"<style>{_HTML_CSS}</style>")
    parts.append("</head>")
    parts.append("<body>")

    # ── Header ─────────────────────────────────────────────────────────────
    llm_cls = "llm-yes" if llm_used else "llm-no"
    llm_text = "Yes" if llm_used else "No"
    parts.append('<div class="report-header">')
    parts.append('<div class="container">')
    parts.append("<h1>Unity Static Asset Audit Report</h1>")
    parts.append(f'<div class="meta">Project: <code>{_esc(project_root)}</code>'
                 f' &middot; Platform: {_esc(platform)}'
                 f' &middot; LLM Enhanced: <span class="llm-badge {llm_cls}">{llm_text}</span></div>')
    parts.append("</div></div>")

    parts.append('<div class="container">')

    # ── Summary cards ──────────────────────────────────────────────────────
    parts.append('<div class="summary-grid">')
    parts.append(f'<div class="summary-card"><div class="num">{len(assets)}</div><div class="label">Assets</div></div>')
    parts.append(f'<div class="summary-card"><div class="num">{len(issues)}</div><div class="label">Issues</div></div>')
    for sev in ("critical", "high", "medium", "low"):
        cnt = severity_counts.get(sev, 0)
        if cnt:
            parts.append(f'<div class="summary-card"><div class="num {sev}">{cnt}</div><div class="label">{sev.capitalize()}</div></div>')
    parts.append(f'<div class="summary-card"><div class="num">{len(fix_decisions)}</div><div class="label">Decisions</div></div>')
    parts.append("</div>")

    # ── Filter bar ─────────────────────────────────────────────────────────
    parts.append('<div class="filter-bar" id="filter-bar">')
    parts.append('<div class="filter-row">')

    # Severity checkboxes
    parts.append('<div class="filter-group">')
    parts.append('<span class="label">Severity</span>')
    all_severities = ["critical", "high", "medium", "low"]
    for sev in all_severities:
        checked = "checked" if severity_counts.get(sev, 0) > 0 else ""
        parts.append(f'<label><input type="checkbox" data-filter="severity" value="{sev}" {checked} onchange="applyFilters()"> {sev.capitalize()}</label>')
    parts.append("</div>")

    # Action checkboxes
    parts.append('<div class="filter-group">')
    parts.append('<span class="label">Action</span>')
    all_actions = [
        ("auto_fix_candidate", "Auto Fix"),
        ("manual_confirm_required", "Manual"),
        ("do_not_fix", "No Fix"),
    ]
    for act_val, act_label in all_actions:
        checked = "checked" if action_counts.get(act_val, 0) > 0 else ""
        parts.append(f'<label><input type="checkbox" data-filter="action" value="{act_val}" {checked} onchange="applyFilters()"> {act_label}</label>')
    parts.append("</div>")

    # Search input
    parts.append('<div class="filter-group">')
    parts.append('<input type="text" id="search-input" placeholder="Search asset path..." oninput="applyFilters()">')
    parts.append("</div>")

    parts.append("</div>")
    parts.append('<div class="filter-stats" id="filter-stats"></div>')
    parts.append("</div>")

    # ── Expand/Collapse all buttons ────────────────────────────────────────
    parts.append('<div class="btn-row">')
    parts.append('<button class="btn" onclick="expandAll()">+ Expand All</button>')
    parts.append('<button class="btn" onclick="collapseAll()">− Collapse All</button>')
    parts.append("</div>")

    # ── Rule sections ──────────────────────────────────────────────────────
    for rule_id in sorted(issues_by_rule.keys()):
        rule_issues = issues_by_rule[rule_id]
        count = len(rule_issues)
        sev = rule_issues[0].severity
        desc = RULE_DESCRIPTIONS.get(rule_id, {})
        what = desc.get("what", rule_issues[0].title)
        why = desc.get("why", rule_issues[0].message)
        fix = desc.get("fix", rule_issues[0].suggestion)

        # Rule action counts
        rule_decisions = [decisions_by_issue[i.issue_id] for i in rule_issues
                          if i.issue_id in decisions_by_issue]
        rule_actions = Counter(d.action for d in rule_decisions)
        auto_n = rule_actions.get("auto_fix_candidate", 0)
        manual_n = rule_actions.get("manual_confirm_required", 0)
        nofix_n = rule_actions.get("do_not_fix", 0)

        rule_section_id = f"rule-{rule_id}"

        parts.append(f'<section class="rule-section" data-rule="{_esc(rule_id)}" id="{rule_section_id}">')

        # Rule header (clickable)
        parts.append(f'<div class="rule-header" onclick="toggleRule(\'{rule_section_id}\')">')
        parts.append(f'<h2><span class="arrow">▼</span> {_esc(rule_id)} '
                     f'<span class="badge badge-{sev}">{sev}</span> '
                     f'<span style="font-weight:400;color:var(--text-secondary);font-size:.9rem">({count} issues)</span></h2>')
        parts.append('<div style="font-size:.8rem;color:var(--text-secondary)">')
        if auto_n:
            parts.append(f'<span class="badge badge-auto">auto: {auto_n}</span> ')
        if manual_n:
            parts.append(f'<span class="badge badge-manual">manual: {manual_n}</span> ')
        if nofix_n:
            parts.append(f'<span class="badge badge-nofix">no fix: {nofix_n}</span>')
        parts.append("</div>")
        parts.append("</div>")

        # Rule description
        parts.append('<div class="rule-desc">')
        parts.append(f"<p><strong>What:</strong> {_esc(what)}</p>")
        parts.append(f"<p><strong>Why:</strong> {_esc(why)}</p>")
        parts.append(f"<p><strong>Fix:</strong> {_esc(fix)}</p>")
        parts.append("</div>")

        # Table
        parts.append('<div class="rule-body">')
        parts.append("<table>")
        parts.append("<thead><tr>")
        parts.append('<th style="width:45%">Asset Path</th>')
        parts.append('<th style="width:12%">Severity</th>')
        parts.append('<th style="width:14%">Action</th>')
        parts.append('<th style="width:12%">Risk</th>')
        parts.append('<th style="width:17%">Reason</th>')
        parts.append("</tr></thead>")
        parts.append("<tbody>")

        for idx, issue in enumerate(sorted(rule_issues, key=lambda i: i.asset_path)):
            dec = decisions_by_issue.get(issue.issue_id)
            action = dec.action if dec else "manual_confirm_required"
            risk = dec.risk_level if dec else "medium"
            reason = dec.reason if dec else ""
            ev = evidence_by_issue.get(issue.issue_id)

            row_id = f"row-{rule_id}-{idx}"
            detail_id = f"detail-{rule_id}-{idx}"

            # Main row
            parts.append(f'<tr class="issue-row" data-severity="{issue.severity}" '
                         f'data-action="{action}" data-path="{_esc(issue.asset_path)}" '
                         f'id="{row_id}" onclick="toggleDetail(\'{detail_id}\',\'{row_id}\')">')
            parts.append(f'<td class="path-cell">{_esc(issue.asset_path)}</td>')
            parts.append(f"<td>{_sev_badge(issue.severity)}</td>")
            parts.append(f"<td>{_act_badge(action)}</td>")
            parts.append(f'<td><span class="badge badge-{risk}">{risk}</span></td>')
            reason_preview = _esc(reason[:100] + ("…" if len(reason) > 100 else "")) if reason else "—"
            parts.append(f"<td>{reason_preview}</td>")
            parts.append("</tr>")

            # Detail row (hidden by default)
            parts.append(f'<tr class="detail-row hidden" id="{detail_id}">')
            parts.append('<td colspan="5">')
            parts.append('<div class="detail-content">')
            parts.append("<dl>")
            parts.append(f"<dt>Issue ID</dt><dd><code>{_esc(issue.issue_id)}</code></dd>")
            parts.append(f"<dt>Title</dt><dd>{_esc(issue.title)}</dd>")
            parts.append(f"<dt>Message</dt><dd>{_esc(issue.message)}</dd>")
            parts.append(f"<dt>Suggestion</dt><dd>{_esc(issue.suggestion)}</dd>")
            if dec:
                parts.append(f"<dt>Decision Reason</dt><dd>{_esc(reason) if reason else '—'}</dd>")
            if issue.evidence:
                evidence_str = _json.dumps(issue.evidence, ensure_ascii=False, indent=2)
                parts.append(f"<dt>Evidence</dt><dd><pre>{_esc(evidence_str)}</pre></dd>")
            if ev:
                parts.append(f"<dt>Context</dt><dd>{_esc(ev.context_summary)}</dd>")
                if ev.risk_hint:
                    parts.append(f"<dt>Risk Hint</dt><dd>{_esc(ev.risk_hint)}</dd>")
                parts.append(f"<dt>Needs Confirm</dt><dd>{ev.need_manual_confirm}</dd>")
                if ev.code_evidence:
                    parts.append('<dt>Code Refs</dt><dd>')
                    parts.append('<details class="code-ref"><summary>Code References</summary><ul>')
                    for ce in ev.code_evidence[:20]:
                        parts.append(f'<li><code>{_esc(ce.file)}</code> line {ce.line}: '
                                     f'<code>{_esc(ce.content)}</code> ({ce.association_type})</li>')
                    parts.append("</ul></details></dd>")
            parts.append("</dl>")
            parts.append("</div>")
            parts.append("</td>")
            parts.append("</tr>")

        parts.append("</tbody>")
        parts.append("</table>")
        parts.append("</div>")  # rule-body
        parts.append("</section>")

    # ── Warnings section ───────────────────────────────────────────────────
    if warnings:
        parts.append('<div class="rule-section">')
        parts.append('<details class="warnings-section" style="padding:14px 20px">')
        parts.append(f"<summary>Scan Warnings ({len(warnings)})</summary>")
        parts.append("<ul>")
        for w in warnings[:50]:
            parts.append(f"<li>{_esc(w)}</li>")
        if len(warnings) > 50:
            parts.append(f"<li>… and {len(warnings) - 50} more</li>")
        parts.append("</ul>")
        parts.append("</details>")
        parts.append("</div>")

    parts.append("</div>")  # container

    # ── JavaScript ─────────────────────────────────────────────────────────
    parts.append('<script type="application/json" id="data-meta">')
    parts.append(meta_json)
    parts.append("</script>")
    parts.append('<script type="application/json" id="data-issues">')
    parts.append(issues_json)
    parts.append("</script>")
    parts.append('<script type="application/json" id="data-decisions">')
    parts.append(decisions_json)
    parts.append("</script>")
    parts.append('<script type="application/json" id="data-evidence">')
    parts.append(evidence_json)
    parts.append("</script>")
    parts.append("<script>")
    parts.append("""
// Parse embedded JSON data
const META = JSON.parse(document.getElementById('data-meta').textContent);
const ISSUES = JSON.parse(document.getElementById('data-issues').textContent);
const DECISIONS = JSON.parse(document.getElementById('data-decisions').textContent);
const EVIDENCE = JSON.parse(document.getElementById('data-evidence').textContent);

// Filter state
function getActiveFilters(type) {
    const checked = document.querySelectorAll(`input[data-filter="${type}"]:checked`);
    return Array.from(checked).map(cb => cb.value);
}

function applyFilters() {
    const activeSeverities = getActiveFilters("severity");
    const activeActions = getActiveFilters("action");
    const searchTerm = (document.getElementById("search-input").value || "").toLowerCase();

    let visibleTotal = 0;
    const ruleCounts = {};  // rule_id -> visible count

    document.querySelectorAll("tr.issue-row").forEach(row => {
        const sev = row.dataset.severity;
        const action = row.dataset.action;
        const path = (row.dataset.path || "").toLowerCase();

        const sevMatch = activeSeverities.includes(sev);
        const actionMatch = activeActions.includes(action);
        const searchMatch = !searchTerm || path.includes(searchTerm);

        if (sevMatch && actionMatch && searchMatch) {
            row.classList.remove("hidden");
            visibleTotal++;
            // Also show the associated detail row (but keep it collapsed)
            const detailId = row.id.replace("row-", "detail-");
            const detailRow = document.getElementById(detailId);
            if (detailRow) detailRow.classList.remove("hidden");

            // Track per-rule counts
            const ruleSection = row.closest(".rule-section");
            if (ruleSection) {
                const ruleId = ruleSection.dataset.rule;
                ruleCounts[ruleId] = (ruleCounts[ruleId] || 0) + 1;
            }
        } else {
            row.classList.add("hidden");
            // Hide associated detail row
            const detailId = row.id.replace("row-", "detail-");
            const detailRow = document.getElementById(detailId);
            if (detailRow) detailRow.classList.add("hidden");
        }
    });

    // Update per-rule section visibility and counters
    document.querySelectorAll(".rule-section").forEach(section => {
        const ruleId = section.dataset.rule;
        const visibleCount = ruleCounts[ruleId] || 0;
        // Update the header count text
        const h2 = section.querySelector(".rule-header h2");
        if (h2) {
            const existingSpan = h2.querySelector(".filtered-count");
            if (existingSpan) existingSpan.remove();
            if (visibleCount > 0) {
                const totalIssues = ISSUES.filter(i => i.rule_id === ruleId).length;
                if (visibleCount !== totalIssues) {
                    const countSpan = document.createElement("span");
                    countSpan.className = "filtered-count";
                    countSpan.style.cssText = "font-weight:400;color:var(--text-secondary);font-size:.9rem";
                    countSpan.textContent = ` (showing ${visibleCount} of ${totalIssues})`;
                    h2.appendChild(countSpan);
                }
            }
        }
        section.style.display = visibleCount > 0 ? "" : "none";
    });

    // Update stats
    document.getElementById("filter-stats").textContent =
        `Showing ${visibleTotal} of ${META.total_issues} issues`;
}

// Toggle detail row
function toggleDetail(detailId, rowId) {
    const detailRow = document.getElementById(detailId);
    if (!detailRow) return;
    detailRow.classList.toggle("hidden");
}

// Toggle rule section collapse
function toggleRule(sectionId) {
    const section = document.getElementById(sectionId);
    if (!section) return;
    const header = section.querySelector(".rule-header");
    const body = section.querySelector(".rule-body");
    if (body) {
        body.classList.toggle("collapsed");
        header.classList.toggle("collapsed");
    }
}

// Expand/collapse all
function expandAll() {
    document.querySelectorAll(".rule-body").forEach(b => b.classList.remove("collapsed"));
    document.querySelectorAll(".rule-header").forEach(h => h.classList.remove("collapsed"));
}
function collapseAll() {
    document.querySelectorAll(".rule-body").forEach(b => b.classList.add("collapsed"));
    document.querySelectorAll(".rule-header").forEach(h => h.classList.add("collapsed"));
}

// Initial filter application
applyFilters();
</script>""")

    parts.append("</body>")
    parts.append("</html>")

    # ── Write file ─────────────────────────────────────────────────────────
    html_content = "\n".join(parts)
    report_path = os.path.join(output_dir, "report.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return report_path


# ═══════════════════════════════════════════════════════════════════════════════
# CI Annotation Output
# ═══════════════════════════════════════════════════════════════════════════════

# Severity → GitHub Actions workflow command level
_GITHUB_SEVERITY_MAP = {
    "critical": "error",
    "high": "warning",
    "medium": "warning",
    "low": "notice",
}

# Severity → GitLab Code Quality severity
_GITLAB_SEVERITY_MAP = {
    "critical": "blocker",
    "high": "major",
    "medium": "minor",
    "low": "info",
}


def generate_ci_annotations(
    output_dir: str,
    issues: list["Issue"],
    fix_decisions: list["FixDecision"],
    evidence_map: dict[str, "EvidenceResult"],
    ci_format: str = "github",
) -> str:
    """Generate CI annotation output for GitHub Actions or GitLab CI.

    GitHub Actions format: ``::warning`` / ``::error`` workflow commands
    written to ``ci-annotations.txt`` with each line being one annotation.

    GitLab CI format: Code Quality JSON artifact written to
    ``gl-code-quality-report.json``.

    Since Unity assets are binary, annotations point at the asset's
    ``.meta`` file (line 1) by default. When code evidence exists with
    a known line number, the annotation points at the ``.cs`` file instead.

    Args:
        output_dir: Directory to write the CI output file to.
        issues: All issues found.
        fix_decisions: Fix decisions for each issue (used for action label).
        evidence_map: Evidence results keyed by issue_id.
        ci_format: ``"github"`` or ``"gitlab"``.

    Returns:
        Path to the generated CI annotation file.
    """

    os.makedirs(output_dir, exist_ok=True)
    decisions_by_issue = {d.issue_id: d for d in fix_decisions}

    if ci_format == "gitlab":
        return _generate_gitlab_annotations(
            output_dir, issues, decisions_by_issue, evidence_map
        )
    else:
        return _generate_github_annotations(
            output_dir, issues, decisions_by_issue, evidence_map
        )


def _generate_github_annotations(
    output_dir: str,
    issues: list["Issue"],
    decisions_by_issue: dict[str, "FixDecision"],
    evidence_map: dict[str, "EvidenceResult"],
) -> str:
    """Generate GitHub Actions workflow command annotations."""
    lines = []

    for issue in issues:
        dec = decisions_by_issue.get(issue.issue_id)
        action = dec.action if dec else "manual_confirm_required"
        sev_level = _GITHUB_SEVERITY_MAP.get(issue.severity, "warning")

        # Default: annotate the .meta file at line 1
        annotate_file = f"Assets/{issue.asset_path}.meta"
        annotate_line = 1

        # If code evidence exists with a line number, use the .cs file instead
        ev = evidence_map.get(issue.issue_id)
        if ev and ev.code_evidence:
            first_ce = ev.code_evidence[0]
            if first_ce.file and first_ce.line:
                annotate_file = first_ce.file
                annotate_line = first_ce.line

        # Build message: [severity] title — action — asset_path
        msg_parts = [f"[{issue.severity}] {issue.title}"]
        if action:
            msg_parts.append(f"— {action.replace('_', ' ')}")
        msg_parts.append(f"— {issue.asset_path}")

        message = " ".join(msg_parts)
        # Escape workflow command characters
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

        lines.append(
            f"::{sev_level} file={annotate_file},line={annotate_line},"
            f"title={issue.rule_id}::{message}"
        )

    output_path = os.path.join(output_dir, "ci-annotations.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return output_path


def _generate_gitlab_annotations(
    output_dir: str,
    issues: list["Issue"],
    decisions_by_issue: dict[str, "FixDecision"],
    evidence_map: dict[str, "EvidenceResult"],
) -> str:
    """Generate GitLab Code Quality JSON artifact."""
    entries = []

    for issue in issues:
        dec = decisions_by_issue.get(issue.issue_id)
        action = dec.action if dec else "manual_confirm_required"
        gl_severity = _GITLAB_SEVERITY_MAP.get(issue.severity, "minor")

        # Default: annotate the .meta file
        location_path = f"Assets/{issue.asset_path}.meta"
        location_lines = {"begin": 1}

        # If code evidence exists, use the .cs file with line
        ev = evidence_map.get(issue.issue_id)
        if ev and ev.code_evidence:
            first_ce = ev.code_evidence[0]
            if first_ce.file and first_ce.line:
                location_path = first_ce.file
                location_lines = {"begin": first_ce.line}

        description = (
            f"{issue.rule_id}: {issue.title} "
            f"[{action.replace('_', ' ')}] — {issue.asset_path}"
        )

        entries.append({
            "description": description,
            "severity": gl_severity,
            "location": {
                "path": location_path,
                "lines": location_lines,
            },
        })

    output_path = os.path.join(output_dir, "gl-code-quality-report.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# Incremental Scan Cache
# ═══════════════════════════════════════════════════════════════════════════════

def save_audit_cache(output_dir: str, issues: list["Issue"],
                     fix_decisions: list["FixDecision"],
                     evidence_map: dict[str, "EvidenceResult"]) -> str:
    """Save audit results to a cache file for incremental scans.

    The cache is keyed by asset_path for fast lookup during merges.
    """
    import json as _json

    os.makedirs(output_dir, exist_ok=True)

    cache = {
        "issues": [
            {
                "issue_id": i.issue_id,
                "rule_id": i.rule_id,
                "severity": i.severity,
                "asset_path": i.asset_path,
                "title": i.title,
                "message": i.message,
                "evidence": i.evidence,
                "suggestion": i.suggestion,
                "auto_fixable": i.auto_fixable,
            }
            for i in issues
        ],
        "fix_decisions": [
            {
                "issue_id": d.issue_id,
                "rule_id": d.rule_id,
                "asset_path": d.asset_path,
                "severity": d.severity,
                "action": d.action,
                "risk_level": d.risk_level,
                "reason": d.reason,
                "suggestion": d.suggestion,
            }
            for d in fix_decisions
        ],
        "evidence": {
            eid: {
                "context_summary": ev.context_summary,
                "risk_hint": ev.risk_hint,
                "need_manual_confirm": ev.need_manual_confirm,
                "association_level": ev.association_level,
                "code_evidence": [
                    {
                        "file": ce.file,
                        "line": ce.line,
                        "content": ce.content,
                        "api": ce.api,
                        "association_type": ce.association_type,
                    }
                    for ce in ev.code_evidence
                ],
            }
            for eid, ev in evidence_map.items()
        },
    }

    cache_path = os.path.join(output_dir, "audit-cache.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        _json.dump(cache, f, ensure_ascii=False, indent=2)

    return cache_path


def load_audit_cache(output_dir: str) -> dict | None:
    """Load cached audit results if they exist.

    Returns None if no cache file exists.
    """
    import json as _json

    cache_path = os.path.join(output_dir, "audit-cache.json")
    if not os.path.exists(cache_path):
        return None

    with open(cache_path, encoding="utf-8") as f:
        return _json.load(f)


def get_git_changed_files(project_root: str, base_ref: str = "HEAD~1") -> set[str] | None:
    """Get the set of changed asset paths from git diff.

    Runs ``git diff --name-only <base_ref> HEAD`` and extracts paths
    under ``Assets/``, stripping the ``Assets/`` prefix.

    Returns None if the git command fails (e.g., not a git repo).
    Returns an empty set if no assets changed.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}..HEAD"],
            capture_output=True, text=True,
            cwd=project_root,
            timeout=10,
        )
        if result.returncode != 0:
            return None

        changed: set[str] = set()
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # Only include files under Assets/
            if line.startswith("Assets/"):
                # Strip "Assets/" prefix to get asset_path
                asset_path = line[len("Assets/"):]
                # Also include without extension (for .meta-less lookups)
                changed.add(asset_path)

        return changed
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _dict_to_issue(d: dict) -> "Issue":
    """Convert a cache dictionary back to an Issue object."""
    from unity_audit.rules.engine import Issue
    return Issue(
        issue_id=d["issue_id"],
        rule_id=d["rule_id"],
        severity=d["severity"],
        asset_path=d["asset_path"],
        title=d["title"],
        message=d["message"],
        evidence=d.get("evidence", {}),
        suggestion=d.get("suggestion", ""),
        auto_fixable=d.get("auto_fixable", False),
    )


def _dict_to_fix_decision(d: dict) -> "FixDecision":
    """Convert a cache dictionary back to a FixDecision object."""
    from unity_audit.fix_planner import FixDecision
    return FixDecision(
        issue_id=d["issue_id"],
        rule_id=d["rule_id"],
        asset_path=d["asset_path"],
        severity=d["severity"],
        action=d["action"],
        risk_level=d["risk_level"],
        reason=d.get("reason", ""),
        suggestion=d.get("suggestion", ""),
    )


def _dict_to_evidence_result(d: dict) -> "EvidenceResult":
    """Convert a cache dictionary back to an EvidenceResult object."""
    from unity_audit.evidence import CodeEvidence, EvidenceResult
    return EvidenceResult(
        issue_id=d.get("issue_id", ""),
        context_summary=d.get("context_summary", ""),
        risk_hint=d.get("risk_hint", ""),
        need_manual_confirm=d.get("need_manual_confirm", True),
        association_level=d.get("association_level", "none"),
        code_evidence=[
            CodeEvidence(
                file=ce.get("file", ""),
                line=ce.get("line", 0),
                content=ce.get("content", ""),
                api=ce.get("api", ""),
                association_type=ce.get("association_type", "none"),
                association_value=ce.get("association_value", ""),
                confidence=ce.get("confidence", 0.0),
            )
            for ce in d.get("code_evidence", [])
        ],
    )
