"""Load and persist the ghosty-agents config (cross-platform)."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - we require >=3.11, kept for clarity
    import tomli as tomllib  # type: ignore

import tomli_w

from ghosty.models import Config


APP_NAME = "ghosty-agents"
CONFIG_FILENAME = "config.toml"


def config_dir() -> Path:
    """Platform-appropriate config directory (honours GHOSTY_CONFIG_DIR)."""
    import os

    override = os.environ.get("GHOSTY_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(typer.get_app_dir(APP_NAME))


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def config_exists() -> bool:
    return config_path().is_file()


def load_config() -> Config:
    """Load config from disk, or return an empty Config if none exists."""
    path = config_path()
    if not path.is_file():
        return Config()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return Config.from_dict(data)


def save_config(config: Config) -> Path:
    """Persist config to disk, creating the directory if needed."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        tomli_w.dump(config.to_dict(), fh)
    return path
