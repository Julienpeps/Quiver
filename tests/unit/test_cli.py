from typer.testing import CliRunner

from quiver.cli import (
    app,
    assessment_completion,
    profile_completion,
    service_completion,
)


def test_cli_displays_help_without_a_command() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Disposable assessment-scoped" in result.output
    assert "gui" in result.output


def test_dynamic_completion_exposes_builtin_profiles_and_services() -> None:
    assert "internal" in profile_completion("in")
    assert "bloodhound-ce" in service_completion("blood")


def test_assessment_completion_filters_persisted_workspaces(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspaces" / "demo"
    workspace.mkdir(parents=True)
    (workspace / ".quiver.yaml").write_text("schema_version: 1\n")
    monkeypatch.setattr("quiver.cli.quiver_root", lambda: tmp_path)

    assert assessment_completion("de") == ["demo"]
