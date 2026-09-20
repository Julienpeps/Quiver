"""Typed domain errors mapped to stable CLI exit codes (spec §19.1)."""

from __future__ import annotations


class VpnError(RuntimeError):
    """VPN configuration, tunnel establishment, or health failure."""


class ImageUnavailableError(RuntimeError):
    """An image cannot be pulled or is missing under an explicit pull policy."""
