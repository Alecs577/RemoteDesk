"""Nomi file sicuri per download RemoteDesk."""

from __future__ import annotations

import os


def safe_download_basename(name: str) -> str | None:
    """Accetta solo un basename senza separatori di percorso."""
    if not name or not isinstance(name, str):
        return None
    name = name.strip()
    if not name or "/" in name or "\\" in name:
        return None
    base = os.path.basename(name)
    if base != name or base in (".", ".."):
        return None
    return base
