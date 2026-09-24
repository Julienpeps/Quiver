from pathlib import Path

from quiver.core.dotfiles import ensure_dotfiles_directory


def test_fixed_global_dotfiles_directory_is_created_privately(tmp_path: Path) -> None:
    directory = ensure_dotfiles_directory(tmp_path)

    assert directory == tmp_path / "dotfiles"
    assert directory.is_dir()
    assert directory.stat().st_mode & 0o777 == 0o700
