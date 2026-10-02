import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import yaml

ROOT = Path(str(files("quiver.resources")))
GENERATOR = ROOT / "images" / "common" / "entrypoint" / "generate-supervisor.py"


def generate(tmp_path: Path, *, gui_enabled: bool) -> str:
    config = tmp_path / "config.yaml"
    output = tmp_path / "quiver-runtime.conf"
    config.write_text(yaml.safe_dump({"gui": {"enabled": gui_enabled}}))
    subprocess.run(
        [sys.executable, str(GENERATOR), str(config), str(output)],
        check=True,
    )
    return output.read_text()


def test_gui_program_autostarts_only_when_gui_is_enabled(tmp_path: Path) -> None:
    assert "autostart=true" in generate(tmp_path, gui_enabled=True)
    assert "autostart=false" in generate(tmp_path, gui_enabled=False)
