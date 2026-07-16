"""Tests for console output safety."""

from ghosty import ui


def test_warning_escapes_untrusted_rich_markup():
    ui.warn("gcloud [/usr/bin/ssh] exited with return code [255]")


def test_error_escapes_untrusted_rich_markup():
    ui.error("permission denied [/usr/bin/ssh]")
