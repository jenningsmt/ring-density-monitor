"""Standalone Elite Dangerous journal watcher.

Tails the live journal file for ring-relevant events:
- Scan: may carry a "Rings" array (FSS/honk auto-scan or full scan)
- FSDJump / Location: carry StarSystem, used to detect system arrival

This module has no dependency on any other project. It is intentionally
minimal: no startup backfill, no dedup cache, no sale-anchoring. Those
behaviors live in the sibling EDMFI-MFI project's journal listener; this
watcher only needs to know "what rings just appeared" and "did the system
change."
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

RING_RELEVANT_EVENTS = frozenset({"Scan", "FSDJump", "Location"})


def parse_journal_line(line: str) -> Optional[dict]:
    """Parse one journal line into an event dict, or None if unusable."""
    if not line or not line.strip():
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("event"):
        return None
    return data


def default_journal_dir() -> Path:
    return Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"


class JournalWatcher:
    """Tails the newest journal file and reports ring-relevant events.

    On first attach, seeks to end-of-file (ignores backlog) so the app
    starts clean rather than replaying an entire session's history. If the
    active journal file rotates (new game session), the new file is read
    from the start.
    """

    def __init__(
        self,
        journal_dir: str | Path | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self._journal_dir = Path(journal_dir) if journal_dir else default_journal_dir()
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current_path: Optional[Path] = None
        self._current_file = None

    def start(self, callback: Callable[[dict], None]) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(callback,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self._close_current()

    def _run(self, callback: Callable[[dict], None]) -> None:
        while not self._stop_event.is_set():
            newest = self._find_newest_file()
            if newest is None:
                time.sleep(self._poll_interval)
                continue
            if newest != self._current_path:
                self._open_newest(newest)
            if self._current_file is None:
                time.sleep(self._poll_interval)
                continue
            line = self._current_file.readline()
            if not line:
                time.sleep(self._poll_interval)
                continue
            event = parse_journal_line(line)
            if event is None:
                continue
            if event.get("event") not in RING_RELEVANT_EVENTS:
                continue
            try:
                callback(event)
            except Exception:
                logger.exception("Ring display callback failed for event %s", event.get("event"))

    def _find_newest_file(self) -> Optional[Path]:
        if not self._journal_dir.exists():
            logger.warning("Journal directory missing: %s", self._journal_dir)
            return None
        candidates = [p for p in self._journal_dir.glob("Journal.*.log") if p.is_file()]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    def _open_newest(self, path: Path) -> None:
        first_attach = self._current_path is None
        self._close_current()
        self._current_path = path
        self._current_file = path.open("r", encoding="utf-8", errors="replace")
        if first_attach:
            self._current_file.seek(0, 2)  # start at live end; ignore backlog
        else:
            self._current_file.seek(0)
        logger.info("Journal watcher attached to %s", path)

    def _close_current(self) -> None:
        if self._current_file:
            self._current_file.close()
        self._current_file = None
