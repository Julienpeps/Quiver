"""System browser integration kept separate from GUI lifecycle logic."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable


class BrowserLauncher:
    """Open local management URLs with the operating system default browser."""

    def __init__(self, opener: Callable[[str], bool] = webbrowser.open) -> None:
        self._opener = opener

    def open(self, url: str) -> bool:
        """Request that the default browser opens a URL."""
        return self._opener(url)
