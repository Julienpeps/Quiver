import json
from datetime import UTC, datetime
from pathlib import Path

from quiver.audit.render import render_asciicast, render_script_output, strip_ansi
from quiver.audit.sessions import (
    append_command,
    create_session,
    new_session_id,
    replay_command,
    update_metadata,
)


def test_session_id_is_utc_sortable() -> None:
    identifier = new_session_id(datetime(2026, 9, 20, 14, 27, 1, tzinfo=UTC))

    assert identifier.startswith("20260920T142701Z-")
    assert len(identifier.rsplit("-", maxsplit=1)[1]) == 6


def test_session_metadata_and_command_are_json_safe(tmp_path: Path) -> None:
    paths = create_session(tmp_path, {"assessment": "demo", "recorder": "script"}, "session")
    append_command(
        paths,
        timestamp=datetime(2026, 9, 20, 14, 30, 41, 131000, tzinfo=UTC),
        cwd="/workspace",
        command='printf "line one\\nline two"',
        exit_code=0,
        duration_ms=8123,
    )
    update_metadata(paths, ended_at="2026-09-20T14:31:00Z")

    command = json.loads(paths.commands.read_text())
    metadata = json.loads(paths.metadata.read_text())
    assert command["command"] == 'printf "line one\\nline two"'
    assert command["ts"] == "2026-09-20T14:30:41.131000Z"
    assert metadata["ended_at"] == "2026-09-20T14:31:00Z"
    assert replay_command(paths, "script") == ["scriptreplay", str(paths.timing), str(paths.output_ansi)]
    assert replay_command(paths, "asciinema") == ["asciinema", "play", str(paths.cast)]


def test_searchable_rendering_removes_ansi_and_cast_metadata(tmp_path: Path) -> None:
    script = tmp_path / "output.ansi"
    text = tmp_path / "output.txt"
    script.write_text("\x1b[31mred\x1b[0m\r\nnext\x07")

    render_script_output(script, text)

    assert text.read_text() == "red\nnext"
    assert strip_ansi("\x1b]8;;https://example.test\x07link\x1b]8;;\x07") == "link"

    cast = tmp_path / "session.cast"
    cast.write_text('{"version": 2}\n[0.1, "o", "\\u001b[1mhello\\u001b[0m\\n"]\n[0.2, "i", "ignored"]\n')
    render_asciicast(cast, text)

    assert text.read_text() == "hello\n"
