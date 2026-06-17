"""Configuration loader for Unity Audit Agent.

Loads optional YAML config file. All values have sensible defaults so the
tool works without any config file.

CLI arguments take precedence over config file values.
"""

import os
from dataclasses import dataclass, field

# Default configuration (used when no config file is present)
DEFAULT_CONFIG = {
    "version": 1,
    "platform": "Unknown",
    "rules": {
        "TEX_UI_MIPMAP_ENABLED": {"enabled": True},
        "TEX_READ_WRITE_ENABLED": {"enabled": True},
        "TEX_UI_MAX_SIZE_TOO_LARGE": {"enabled": True, "max_size": 1024},
        "TEX_NPOT_DETECTED": {"enabled": True},
        "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD": {"enabled": True, "duration_seconds": 10},
        "AUD_STEREO_SFX": {"enabled": True},
        "PREFAB_MISSING_SCRIPT": {"enabled": True},
        "UI_TOO_MANY_GRAPHIC_RAYCASTERS": {"enabled": True},
    },
    "agent": {
        "enabled": False,
        "max_steps": 12,
        "timeout_seconds": 60,
        "trace_enabled": True,
        "max_workers": 5,
    },
}

# Known config keys for validation
KNOWN_TOP_KEYS = {"version", "platform", "rules", "agent"}
KNOWN_RULE_IDS = set(DEFAULT_CONFIG["rules"].keys())
KNOWN_AGENT_KEYS = {"enabled", "max_steps", "timeout_seconds", "trace_enabled", "model", "max_workers"}


@dataclass
class AuditConfig:
    """Resolved audit configuration."""
    platform: str = "Unknown"
    rules: dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["rules"]))
    agent: dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["agent"]))
    _warnings: list[str] = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)


def load_config(config_path: str | None = None) -> AuditConfig:
    """Load configuration from a YAML file, with defaults for missing values.

    Args:
        config_path: Path to a YAML config file. If None, returns defaults.

    Returns:
        AuditConfig with resolved settings.

    Raises:
        ValueError: If config has invalid types or illegal values.
    """
    cfg = AuditConfig()

    if config_path is None:
        return cfg

    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except ImportError:
        cfg._warnings.append("PyYAML not installed, using defaults")
        return cfg
    except Exception as e:
        raise ValueError(f"Failed to parse config file {config_path}: {e}") from e

    if data is None:
        return cfg

    if not isinstance(data, dict):
        raise ValueError("Config file must contain a YAML mapping at top level")

    # Validate top-level keys
    unknown_keys = set(data.keys()) - KNOWN_TOP_KEYS
    for key in unknown_keys:
        cfg._warnings.append(f"Unknown config key: {key}")

    # Load platform
    if "platform" in data:
        if not isinstance(data["platform"], str):
            raise ValueError("config.platform must be a string")
        cfg.platform = data["platform"]

    # Load rules
    if "rules" in data:
        rules_data = data["rules"]
        if not isinstance(rules_data, dict):
            raise ValueError("config.rules must be a mapping")
        for rule_id, rule_cfg in rules_data.items():
            if rule_id not in KNOWN_RULE_IDS:
                cfg._warnings.append(f"Unknown rule ID in config: {rule_id}")
                continue
            if not isinstance(rule_cfg, dict):
                raise ValueError(f"config.rules.{rule_id} must be a mapping")
            # Merge with defaults
            merged = dict(DEFAULT_CONFIG["rules"][rule_id])
            merged.update(rule_cfg)
            cfg.rules[rule_id] = merged

    # Load agent
    if "agent" in data:
        agent_data = data["agent"]
        if not isinstance(agent_data, dict):
            raise ValueError("config.agent must be a mapping")
        unknown_agent_keys = set(agent_data.keys()) - KNOWN_AGENT_KEYS
        for key in unknown_agent_keys:
            cfg._warnings.append(f"Unknown agent config key: {key}")
        cfg.agent.update(agent_data)

        # Validate agent values
        if "max_steps" in agent_data:
            ms = agent_data["max_steps"]
            if not isinstance(ms, int) or ms < 1:
                raise ValueError("agent.max_steps must be a positive integer")
        if "timeout_seconds" in agent_data:
            ts = agent_data["timeout_seconds"]
            if not isinstance(ts, (int, float)) or ts <= 0:
                raise ValueError("agent.timeout_seconds must be a positive number")

    return cfg


def merge_cli_with_config(
    config: AuditConfig,
    cli_platform: str | None = None,
    cli_agent: bool = False,
    cli_model: str | None = None,
    cli_max_steps: int | None = None,
    cli_max_workers: int | None = None,
) -> AuditConfig:
    """Merge CLI arguments into config, with CLI taking precedence.

    Args:
        config: Loaded AuditConfig.
        cli_platform: --platform from CLI.
        cli_agent: --agent flag from CLI.
        cli_model: --model from CLI.
        cli_max_steps: --max-agent-steps from CLI.
        cli_max_workers: --max-workers from CLI.

    Returns:
        Updated AuditConfig (mutates in place, but also returns for convenience).
    """
    if cli_platform is not None:
        config.platform = cli_platform

    if cli_agent:
        config.agent["enabled"] = True

    if cli_model is not None:
        config.agent["model"] = cli_model

    if cli_max_steps is not None:
        config.agent["max_steps"] = cli_max_steps

    if cli_max_workers is not None:
        config.agent["max_workers"] = cli_max_workers

    return config
