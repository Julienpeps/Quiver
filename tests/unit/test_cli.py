from typing import get_type_hints

from typer.testing import CliRunner

from quiver.cli import (
    AssessmentName,
    app,
    assessment_completion,
    profile_completion,
    service_app,
    service_completion,
)


def test_cli_displays_help_without_a_command(monkeypatch) -> None:
    # Pin a wide terminal: rich-rendered help truncates long option names
    # (e.g. "--packages" -> "--pack...") on narrow terminals such as CI runners.
    monkeypatch.setenv("COLUMNS", "200")

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Disposable assessment-scoped" in result.output
    assert "gui" in result.output
    start = CliRunner().invoke(app, ["start", "--help"])
    edit = CliRunner().invoke(app, ["edit", "--help"])
    assert "--packages" in start.output
    assert "--packages" in edit.output


def test_all_assessment_commands_use_dynamic_name_completion() -> None:
    for command in [*app.registered_commands, *service_app.registered_commands]:
        callback = command.callback
        hints = get_type_hints(callback, include_extras=True) if callback is not None else {}
        if "name" in hints:
            assert hints["name"] == AssessmentName


def test_dynamic_completion_exposes_builtin_profiles_and_services() -> None:
    assert "internal" in profile_completion("in")
    assert "bloodhound-ce" in service_completion("blood")


def test_assessment_completion_filters_persisted_workspaces(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspaces" / "demo"
    workspace.mkdir(parents=True)
    (workspace / ".quiver.yaml").write_text("schema_version: 1\n")
    monkeypatch.setattr("quiver.cli.quiver_root", lambda: tmp_path)

    assert assessment_completion("de") == ["demo"]
