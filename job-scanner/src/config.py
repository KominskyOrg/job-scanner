"""
Configuration loader for job scanner pipeline.
Loads settings from config/pipeline.json at import time.
Provides singleton access to config values.
"""

import json
import os
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""
    pass


# Module-level config cache (singleton)
_config: dict | None = None
_config_path: Path | None = None
_fallback_used: bool = False


def _get_config_path() -> Path:
    """Get the path to pipeline.json config file."""
    # Resolve relative to this file's location
    src_dir = Path(__file__).parent
    base_dir = src_dir.parent
    return base_dir / "config" / "pipeline.json"


def _load_config() -> dict:
    """Load configuration from pipeline.json."""
    global _config, _config_path

    if _config is not None:
        return _config

    config_path = _get_config_path()
    _config_path = config_path

    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        with open(config_path) as f:
            _config = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config file: {e}")

    # Validate required sections
    required_sections = ["version", "paths", "enums", "thresholds"]
    for section in required_sections:
        if section not in _config:
            raise ConfigError(f"Missing required config section: {section}")

    return _config


def get_config() -> dict:
    """
    Get the full configuration dictionary.
    Loads from disk on first call, returns cached value after.
    """
    return _load_config()


def get_threshold(name: str) -> int | float | bool:
    """
    Get a threshold value by name.

    Args:
        name: Threshold name (e.g., 'apply_min_score', 'consider_min_score')

    Returns:
        The threshold value

    Raises:
        ConfigError: If threshold doesn't exist
    """
    config = get_config()
    thresholds = config.get("thresholds", {})

    if name not in thresholds:
        raise ConfigError(f"Unknown threshold: {name}")

    return thresholds[name]


def get_enum_values(name: str) -> list[str]:
    """
    Get allowed values for an enum by name.

    Args:
        name: Enum name (e.g., 'final_decision', 'risk_level')

    Returns:
        List of allowed values

    Raises:
        ConfigError: If enum doesn't exist
    """
    config = get_config()
    enums = config.get("enums", {})

    if name not in enums:
        raise ConfigError(f"Unknown enum: {name}")

    return enums[name]


def validate_enum(name: str, value: str) -> bool:
    """
    Check if a value is valid for a given enum.

    Args:
        name: Enum name
        value: Value to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        allowed = get_enum_values(name)
        return value in allowed
    except ConfigError:
        return False


def get_path(name: str) -> Path:
    """
    Get a configured path by name.

    Args:
        name: Path key (e.g., 'output_dir')

    Returns:
        Path object (resolved relative to base_dir)

    Raises:
        ConfigError: If path doesn't exist
    """
    config = get_config()
    paths = config.get("paths", {})

    if name not in paths:
        raise ConfigError(f"Unknown path: {name}")

    # Resolve relative to base directory
    src_dir = Path(__file__).parent
    base_dir = src_dir.parent

    path_value = paths[name]
    if isinstance(path_value, str):
        return base_dir / path_value
    elif isinstance(path_value, dict):
        # Nested paths (e.g., prompts.evaluator)
        raise ConfigError(f"Path '{name}' is a nested config, use dot notation")

    return base_dir / str(path_value)


def get_prompt_path(prompt_name: str) -> Path:
    """
    Get a prompt file path by name.

    Args:
        prompt_name: Prompt key (e.g., 'evaluator', 'writer_cover_letter')

    Returns:
        Path to the prompt file

    Raises:
        ConfigError: If prompt path doesn't exist
    """
    config = get_config()
    prompts = config.get("paths", {}).get("prompts", {})

    if prompt_name not in prompts:
        raise ConfigError(f"Unknown prompt: {prompt_name}")

    src_dir = Path(__file__).parent
    base_dir = src_dir.parent

    return base_dir / prompts[prompt_name]


def get_scrape_setting(name: str) -> Any:
    """
    Get a scrape setting by name.

    Args:
        name: Setting name (e.g., 'store_raw_html', 'min_description_length')

    Returns:
        The setting value

    Raises:
        ConfigError: If setting doesn't exist
    """
    config = get_config()
    settings = config.get("scrape_settings", {})

    if name not in settings:
        raise ConfigError(f"Unknown scrape setting: {name}")

    return settings[name]


def get_stage2_mode() -> str:
    """
    Get the Stage 2 processing mode.

    Returns:
        'strict' or 'flexible'
    """
    config = get_config()
    return config.get("stage2_mode", "strict")


def get_output_dir() -> Path:
    """
    Get the output directory path.
    Convenience function for common use case.

    Returns:
        Path to output directory
    """
    return get_path("output_dir")


def reload_config():
    """
    Force reload of configuration from disk.
    Useful for testing or after config changes.
    """
    global _config, _fallback_used
    _config = None
    _fallback_used = False
    _load_config()


def is_fallback_mode() -> bool:
    """
    Check if config is running in fallback mode.

    Returns:
        True if fallback mode is active due to missing/invalid config
    """
    return _fallback_used


# Load config at module import time
try:
    _load_config()
except ConfigError as e:
    if os.getenv("ALLOW_CONFIG_FALLBACK", "").lower() == "true":
        _fallback_used = True
        print(f"[CONFIG] WARNING: Running in fallback mode - {e}")
    else:
        raise ConfigError(f"Config required. Set ALLOW_CONFIG_FALLBACK=true to override: {e}")
