"""Conversion of terminal recordings into searchable plain text."""

from __future__ import annotations

import json
import re
from pathlib import Path

# CSI and OSC sequences account for conventional terminal styling and hyperlinks.
_ESCAPE_SEQUENCE = re.compile(
    r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])"
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    """Remove terminal control sequences while retaining line breaks and tabs."""
    return _CONTROL_CHARACTERS.sub("", _ESCAPE_SEQUENCE.sub("", text))


def render_script_output(source: Path, destination: Path) -> None:
    """Render a util-linux script output stream as UTF-8 searchable text."""
    destination.write_text(strip_ansi(source.read_text(errors="replace")))
    destination.chmod(0o600)


def render_asciicast(source: Path, destination: Path) -> None:
    """Extract output events from a local asciicast v2/v3 recording."""
    output: list[str] = []
    for line in source.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(event, list)
            and len(event) >= 3
            and event[1] == "o"
            and isinstance(event[2], str)
        ):
            output.append(event[2])
    destination.write_text(strip_ansi("".join(output)))
    destination.chmod(0o600)
