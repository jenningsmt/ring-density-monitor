"""Persisted settings for Ring Density Monitor.

Stored under %APPDATA%\\RingDensityMonitor\\config.json (Windows), deliberately
independent of where the app's own executable/script lives, since a frozen
exe on the Desktop has no git checkout to anchor to. No UI writes this file;
it exists for the rare non-standard install where the journal isn't in the
default Saved Games location -- hand-edit journal_dir to override it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

APP_NAME = "RingDensityMonitor"

DEFAULT_CONFIG: dict[str, Any] = {
    "journal_dir": None,  # None -> use core.journal_watcher.default_journal_dir()
}


def config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / APP_NAME / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def resolve_journal_dir(cli_override: Optional[str]) -> Optional[str]:
    """CLI flag > config.json > built-in default (None lets the caller fall
    back to core.journal_watcher.default_journal_dir())."""
    if cli_override:
        return cli_override
    configured = load_config().get("journal_dir")
    if configured:
        return str(configured)
    return None
