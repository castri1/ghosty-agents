"""Shared console + output helpers (with a little ghostly personality)."""

from __future__ import annotations

import random
import sys
from typing import Optional, Sequence

import typer
from rich.align import Align
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

try:
    import questionary

    _HAS_QUESTIONARY = True
except Exception:  # pragma: no cover - optional dependency guard
    _HAS_QUESTIONARY = False

_CUSTOM = "\x00__ghosty_custom__"

console = Console()
err_console = Console(stderr=True)

GHOST = "👻"

_GHOST_ART = r"""
       .-.
     .'   `.
     |  o o |     ghosty-agents
     |   ^  |     summon a fleet of ghosts in the cloud
     |  \_/ |
     '.___.'
      |||||
"""

_QUIPS = [
    "Let's raise some ghosts. 👻",
    "Time to haunt the cloud. 👻",
    "Spinning up spectral compute. 👻",
    "No bodies, just boo-tiful VMs. 👻",
]


def step(msg: str) -> None:
    console.print(f"[bold cyan]›[/] {escape(str(msg))}")


def success(msg: str) -> None:
    console.print(f"[bold green]✓[/] {escape(str(msg))}")


def skip(msg: str) -> None:
    console.print(f"[dim]· {escape(str(msg))}[/]")


def warn(msg: str) -> None:
    err_console.print(f"[bold yellow]![/] {escape(str(msg))}")


def error(msg: str) -> None:
    err_console.print(f"[bold red]✗[/] {escape(str(msg))}")


def info(msg: str) -> None:
    console.print(f"[cyan]{escape(str(msg))}[/]")


def banner(subtitle: Optional[str] = None) -> None:
    """Print the ghost banner. Subtitle overrides the random quip."""
    art = Text(_GHOST_ART, style="bold magenta")
    tagline = subtitle or random.choice(_QUIPS)
    console.print(art)
    console.print(Align.left(Text(tagline, style="dim italic")))
    console.print()


def panel(body: str, *, title: str = "", style: str = "cyan") -> None:
    console.print(Panel(body, title=title, border_style=style, expand=False))


def celebrate(msg: str) -> None:
    """A happy, boxed success message."""
    console.print(
        Panel(Text(f"{GHOST}  {msg}", style="bold green"),
              border_style="green", expand=False)
    )


def _interactive_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # pragma: no cover
        return False


def is_interactive_tty() -> bool:
    """Return whether the current process can safely open interactive prompts."""
    return _interactive_tty()


def choose(
    title: str,
    options: Sequence[tuple[str, str]],
    *,
    default_index: int = 0,
    allow_custom: bool = True,
    custom_label: str = "Enter a different value",
) -> str:
    """Arrow-key chooser. options = [(value, label), ...]. Returns chosen value.

    Uses questionary (↑/↓ + Enter) on an interactive terminal; falls back to a
    numbered text prompt otherwise (piped input, CI, no questionary).
    Labels should be plain text (no rich markup) so they render in both modes.
    """
    if not options:
        return typer.prompt(title)

    if _HAS_QUESTIONARY and _interactive_tty():
        choices = [questionary.Choice(title=label, value=val) for val, label in options]
        if allow_custom:
            choices.append(questionary.Choice(title=custom_label, value=_CUSTOM))
        default_value = options[default_index][0] if 0 <= default_index < len(options) else None
        answer = questionary.select(
            title,
            choices=choices,
            default=default_value,
            qmark=GHOST,
            instruction="(use ↑/↓ arrows, Enter to confirm)",
            pointer="❯",
        ).ask()
        if answer is None:  # Ctrl-C / Esc
            raise typer.Abort()
        if answer == _CUSTOM:
            return typer.prompt("Value")
        return answer

    # --- fallback: numbered prompt --------------------------------------
    console.print(f"[bold]{title}[/]")
    for i, (_val, label) in enumerate(options, 1):
        marker = "[green]●[/]" if (i - 1) == default_index else "[dim]○[/]"
        console.print(f"  {marker} [bold cyan]{i}[/]) {label}")
    custom_n = len(options) + 1
    if allow_custom:
        console.print(f"  [dim]○[/] [bold cyan]{custom_n}[/]) {custom_label}")

    while True:
        raw = typer.prompt("Choice", default=str(default_index + 1))
        try:
            n = int(raw)
        except ValueError:
            warn("Enter the number of your choice.")
            continue
        if 1 <= n <= len(options):
            return options[n - 1][0]
        if allow_custom and n == custom_n:
            return typer.prompt("Value")
        warn(f"Pick a number between 1 and {custom_n if allow_custom else len(options)}.")
