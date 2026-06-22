# Unity Static Asset Audit Agent v0.2.0

A deterministic static analysis tool for Unity projects that scans assets,
identifies issues via rule engine, gathers evidence, plans fixes, and produces
structured reports. Optionally runs a single-agent harness with LLM for
enhanced risk assessment.

## Quick Start

### Installation

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
```

Requirements: Python 3.10+, Pillow, PyYAML.

### Basic Scan

```bash
python -m unity_audit.cli scan /path/to/UnityProject --platform Android --output ./outputs
```

Outputs (in `./outputs/`):
- `assets.json` — all scanned assets
- `issues.json` — all issues found
- `fix_decisions.json` — fix decision per issue
- `report.md` — human-readable summary report

### Agent Mode (with LLM)

```bash
python -m unity_audit.cli scan /path/to/UnityProject \
  --platform Android \
  --agent \
  --model claude-sonnet-4-6 \
  --output ./outputs
```

Additional outputs:
- `run.json` — full run state (checkpoint)
- `trace.jsonl` — structured event trace
- `agent_assessments.json` — agent assessments per issue
- `agent_fix_plans.json` — structured, approval-required candidate fix plans

Without an API key, agent mode falls back to deterministic results automatically.

### Record Human Feedback

Store a reviewed decision in the Unity project so future Agent runs can use it
as project-specific context:

```bash
python -m unity_audit.cli feedback /path/to/UnityProject \
  --rule-id TEX_READ_WRITE_ENABLED \
  --asset-pattern 'Textures/Runtime/**' \
  --decision rejected_fix \
  --reason 'Runtime-generated textures are read by gameplay code'
```

Feedback is appended to `.unity-audit/feedback.jsonl` inside the Unity project.
It is advisory only and cannot override direct code evidence or deterministic
guardrails.

### Testing

```bash
# Run all tests (no network required)
python -m pytest -q

# Run specific test groups
python -m pytest tests/unit/ -q        # Unit tests only
python -m pytest tests/integration/ -q  # Integration tests
```

All tests use local fixtures and fake models — no network or Unity Editor needed.

## Configuration

Optional YAML config file:

```yaml
version: 1
platform: Android

rules:
  TEX_UI_MIPMAP_ENABLED:
    enabled: true
  TEX_UI_MAX_SIZE_TOO_LARGE:
    enabled: true
    max_size: 1024
  AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD:
    enabled: true
    duration_seconds: 10

agent:
  enabled: false
  max_steps: 12
  timeout_seconds: 60
  trace_enabled: true
```

Usage: `python -m unity_audit.cli scan ... --config audit.yaml`

CLI arguments override config file values.

## CLI Reference

```
scan <project> [options]

Positional:
  project              Path to Unity project root

Options:
  --platform PLATFORM  Target platform (Android, iOS, WebGL, ...)
  --output, -o DIR     Output directory (default: ./outputs)
  --config, -c FILE    Path to YAML config file
  --agent              Enable Agent mode with LLM
  --model MODEL        Model name (e.g., claude-sonnet-4-6, fake:test)
  --max-agent-steps N  Max agent steps (default: 12)
  --resume FILE        Resume from run.json checkpoint
  --no-trace           Disable trace.jsonl output
```

Exit codes:
- `0` — scan completed (including agent fallback)
- `1` — project path, config, or scan error
- `2` — output directory or report write error

## Architecture

```
CLI → AuditService (deterministic pipeline)
    → HarnessRunner (optional agent loop)
    → ReportGenerator

Deterministic Pipeline:
  Scanner → MetaParser → Extractors → RuleEngine → Evidence → FixPlanner

Agent Harness:
  Tools (read-only) → Policy (guardrails) → State/Trace → Runner
  ModelClient (fake or real) → AuditAgent → Structured Assessment
```

Agent assessments may include structured usage context, evidence strength, and
candidate fix plans. Fix plans never execute directly and must declare
`requires_approval: true`.

Key principles:
- **Deterministic rules are the source of truth** — LLM cannot add, remove, or
  modify issues, rule IDs, or severities.
- **Agent is a context-aware assessor** — it calls read-only tools to gather
  evidence, then produces structured assessments.
- **Always completes** — if the agent/LLM fails, the system falls back to
  deterministic fix planner results.
- **Secure by default** — all tool paths are sandboxed, no shell execution,
  no write tools in this version.

## Project Structure

```
unity_audit/
  application/         # AuditService, models
  harness/             # Runner, tools, state, tracing, policy, approvals
  agents/              # AuditAgent, model_client, prompts, schemas
  extractors/          # Texture, Audio, Prefab/Scene extractors
  rules/               # Rule engine + 8 deterministic rules
  config.py            # YAML config loader
  cli.py               # CLI entry point
  evidence.py          # Evidence builder with association levels
  fix_planner.py       # Fix decision planner
  meta_parser.py       # Unity .meta file parser
  report.py            # JSON + Markdown report generator
  scanner.py           # Project scanner

tests/
  unit/                # Unit tests (140+ tests)
  integration/         # CLI & end-to-end tests
  fixtures/            # Test projects
```

## Supported Asset Types

| Type | Extensions | Checks |
|------|-----------|--------|
| Texture | .png, .jpg, .jpeg, .tga, .psd | Mipmap, Read/Write, Max Size, NPOT |
| Audio | .wav, .mp3, .ogg | Load Type, Stereo/Mono, Duration |
| Prefab | .prefab | Missing Script, GraphicRaycaster |
| Scene | .unity | Missing Script, GraphicRaycaster |

## Evidence Levels (Read/Write Check)

- `direct` — code directly references the asset by name/path/GUID and uses pixel APIs
- `possible` — pixel APIs exist in the project but no direct link to this asset
- `none` — no relevant API usage found

See `tests/fixtures/evidence_project/` for example setup.

## License

Internal tool — no license specified.
