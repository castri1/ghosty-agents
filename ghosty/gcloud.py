"""Thin subprocess wrapper around the gcloud CLI.

Python port of the gc() helper from scripts/lib.sh. Every call is pinned to the
isolated gcloud configuration, the account, and (unless no_project=True) the
project — so a concurrent gcloud session in another project is neither disturbed
by nor able to disturb these commands.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Sequence

from ghosty.models import Config


class GcloudNotFound(RuntimeError):
    """Raised when the gcloud CLI is not on PATH."""


class GcloudError(RuntimeError):
    """A gcloud invocation returned a non-zero exit code."""

    def __init__(self, args: Sequence[str], returncode: int, stderr: str):
        self.args_list = list(args)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"gcloud {' '.join(args)} failed ({returncode}):\n{self.stderr}"
        )


def gcloud_available() -> bool:
    return shutil.which("gcloud") is not None


def _require_gcloud() -> None:
    if not gcloud_available():
        raise GcloudNotFound(
            "gcloud CLI not found on PATH. Install the Google Cloud SDK: "
            "https://cloud.google.com/sdk/docs/install"
        )


def _global_flags(config: Config, no_project: bool) -> list[str]:
    flags: list[str] = []
    if config.gcloud_config_name:
        flags += ["--configuration", config.gcloud_config_name]
    if config.account:
        flags += ["--account", config.account]
    if not no_project and config.project_id:
        flags += ["--project", config.project_id]
    return flags


def run(
    config: Config,
    args: Sequence[str],
    *,
    no_project: bool = False,
    check: bool = True,
    capture: bool = True,
    raw: bool = False,
) -> subprocess.CompletedProcess:
    """Run a gcloud command with the pinned global flags.

    args: the gcloud subcommand + flags WITHOUT the leading "gcloud", e.g.
          ["compute", "instances", "list"].
    no_project: omit --project (for `projects create`, billing, budgets).
    capture: capture stdout/stderr (False = inherit, e.g. for interactive ssh).
    raw: inject NO global flags at all. Use for discovery calls during `init`,
         before the isolated configuration exists.
    """
    _require_gcloud()
    flags = [] if raw else _global_flags(config, no_project)
    cmd = ["gcloud", *args, *flags]
    proc = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )
    if check and proc.returncode != 0:
        stderr = proc.stderr if capture and proc.stderr else ""
        raise GcloudError(args, proc.returncode, stderr)
    return proc


def run_json(config: Config, args: Sequence[str], *, no_project: bool = False, raw: bool = False):
    """Run a gcloud command with --format=json and parse the output."""
    proc = run(config, [*args, "--format=json"], no_project=no_project, raw=raw)
    out = (proc.stdout or "").strip()
    if not out:
        return []
    return json.loads(out)


def exists(config: Config, args: Sequence[str], *, no_project: bool = False) -> bool:
    """True if a `... describe ...` style command succeeds (resource exists)."""
    proc = run(config, args, no_project=no_project, check=False)
    return proc.returncode == 0


def interactive(
    config: Config, args: Sequence[str], *, no_project: bool = False
) -> int:
    """Run a gcloud command attached to the terminal (e.g. ssh). Returns exit code."""
    _require_gcloud()
    cmd = ["gcloud", *args, *_global_flags(config, no_project)]
    return subprocess.run(cmd).returncode
