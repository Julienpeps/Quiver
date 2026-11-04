import asyncio
from pathlib import Path

from quiver.mcp.server import QuiverMcp, WorkspacePatch, create_server


def test_mcp_server_registers_workspace_and_execution_tools(tmp_path: Path) -> None:
    server = create_server(QuiverMcp(tmp_path))

    async def names() -> set[str]:
        return {tool.name for tool in await server.list_tools()}

    registered = asyncio.run(names())
    assert {"workspace_create", "workspace_destroy", "exec_start", "exec_read"} <= registered


def test_workspace_patch_validates_and_persists_typed_changes(tmp_path: Path) -> None:
    app = QuiverMcp(tmp_path)
    app.workspace_create("demo")

    app.workspace_patch("demo", WorkspacePatch(privileged=True, network_mode="none", gui_enabled=False))

    config = app.workspace_get("demo")
    assert config["docker"]["privileged"] is True
    assert config["docker"]["network"]["mode"] == "none"
    assert config["gui"]["enabled"] is False


def test_mcp_http_transport_is_localhost_by_default(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeServer:
        def run(self, transport: str, **kwargs: object) -> None:
            calls.append((transport, kwargs))

    monkeypatch.setattr("quiver.mcp.server.create_server", lambda _application: FakeServer())
    from quiver.mcp.server import run

    run(tmp_path, transport="streamable-http", port=8123)

    assert calls == [("streamable-http", {"host": "127.0.0.1", "port": 8123})]


def test_workspace_destroy_requires_a_plan_token(tmp_path: Path) -> None:
    app = QuiverMcp(tmp_path)
    app.workspace_create("demo")
    plan = app.destroy_plan("demo")

    destroyed = app.destroy("demo", plan["confirmation_token"])

    assert destroyed["destroyed"] is True
    assert not (tmp_path / "workspaces" / "demo").exists()
