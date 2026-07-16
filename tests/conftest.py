"""Global pytest isolation for user-local Ghosty config."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_user_config(tmp_path, monkeypatch):
    """Keep tests from reading or writing the real app config directory."""
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path / "ghosty-config"))
