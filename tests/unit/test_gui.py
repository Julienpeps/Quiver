from importlib.resources import files
from pathlib import Path

from quiver.core.browser import BrowserLauncher
from quiver.core.gui import GuiManager, gui_url
from quiver.core.lifecycle import AssessmentStatus
from quiver.docker.backend import CommandResult
from quiver.models.config import AssessmentConfig


def config(tmp_path: Path, **gui: object) -> AssessmentConfig:
    return AssessmentConfig.model_validate(
        {
            "schema_version": 1,
            "name": "demo",
            "image": {"profile": "base"},
            "workspace": {"path": str(tmp_path)},
            "gui": gui,
        }
    )


def test_gui_url_uses_novnc_remote_resize() -> None:
    assert gui_url("127.0.0.1", 49152) == (
        "http://127.0.0.1:49152/vnc.html?autoconnect=true&resize=remote"
    )


def test_gui_script_keeps_vnc_private_without_authentication() -> None:
    script = (Path(str(files("quiver.resources"))) / "images/common/gui/quiver-gui").read_text()

    assert "gui-password" not in script
    assert "vncpasswd -f" not in script
    assert "-rfbauth" not in script
    assert "-SecurityTypes None" in script
    assert "-localhost yes" in script
    assert "-SendCutText=on" in script
    assert "-SendClipboard" not in script
    assert "rm -f /etc/machine-id" in script
    assert "dbus-uuidgen --ensure=/etc/machine-id" in script
    assert "dbus-run-session" in script
    assert "HOME=/workspace/.quiver/home" in script
    assert "XDG_CONFIG_HOME=/run/quiver/xfce-config" in script
    assert "xfce4-session" in script
    assert "xfconf-query --channel xfce4-desktop" in script
    assert "/backdrop/screen0/monitorVNC-0/workspace0/last-image" in script
    assert "/backdrop/screen0/monitorVNC-0/workspace0/image-style" in script
    assert "/usr/share/backgrounds/quiver/background.png" in script
    assert "websockify --web /opt/novnc" in script
    assert "wait -n \"$vnc_pid\" \"$xfce_pid\" \"$websockify_pid\"" in script
    assert "5901" in script


class FakeLifecycle:
    def __init__(self) -> None:
        self.docker = FakeDocker()

    def status(self, assessment: AssessmentConfig) -> AssessmentStatus:
        return AssessmentStatus(
            assessment.name, "quiver-demo", True, "running", "quiver-base", Path(assessment.workspace.path)
        )

    def start(self, assessment: AssessmentConfig) -> AssessmentStatus:
        raise AssertionError("container should already be running")


class FakeDocker:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(self, *args: str, check: bool = True) -> CommandResult:
        self.commands.append(args)
        if args[-2:] == ("status", "gui"):
            return CommandResult(tuple(args), 0, "gui RUNNING pid 1", "")
        return CommandResult(tuple(args), 0, "gui: started", "")

    def inspect(self, resource: str) -> dict[str, object]:
        return {
            "NetworkSettings": {
                "Ports": {"6080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]}
            }
        }


def test_gui_manager_discovers_dynamic_port_and_opens_browser(tmp_path: Path) -> None:
    opened: list[str] = []

    lifecycle = FakeLifecycle()
    access = GuiManager(lifecycle).open(  # type: ignore[arg-type]
        config(tmp_path), browser=BrowserLauncher(lambda url: opened.append(url) or True)
    )

    assert access.url == gui_url("127.0.0.1", 49152)
    assert opened == [access.url]
    assert access.warning is None
    assert lifecycle.docker.commands[0] == (
        "exec",
        "quiver-demo",
        "supervisorctl",
        "-c",
        "/etc/supervisor/supervisord.conf",
        "start",
        "gui",
    )


def test_gui_manager_warns_for_explicit_non_loopback_binding(tmp_path: Path) -> None:
    assessment = config(tmp_path, host_ip="0.0.0.0")
    lifecycle = FakeLifecycle()
    lifecycle.docker.inspect = lambda resource: {  # type: ignore[method-assign]
        "NetworkSettings": {
            "Ports": {"6080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "6080"}]}
        }
    }

    access = GuiManager(lifecycle).open(assessment, launch_browser=False)  # type: ignore[arg-type]

    assert access.warning == "GUI is exposed on non-loopback address 0.0.0.0"
