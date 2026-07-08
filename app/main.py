"""Entrypoint: live ring density monitor.

Watches the Elite Dangerous journal, and for every FSS/full Scan event that
reveals a ring, shows body/ring/type/density/delta-from-galactic-norm —
color-coded so a glance tells you whether it's worth a DSS pass.
"""

from __future__ import annotations

import argparse
import queue
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

from app.config import resolve_journal_dir
from app.ring_table import RingTable
from core.journal_watcher import JournalWatcher, default_journal_dir
from core.ring_display import RingRow, build_ring_rows, create_scorer

_QUEUE_POLL_MS = 150
_PURGE_TICK_MS = 1000


def run(journal_dir: Optional[str] = None) -> None:
    root = tk.Tk()
    root.title("Ring Density Monitor")
    root.geometry("1400x600")

    status_var = tk.StringVar(value="Waiting for journal events...")
    status_label = tk.Label(root, textvariable=status_var, anchor="w", fg="#555555")
    status_label.pack(fill="x", padx=6, pady=(6, 0))

    table = RingTable(root)
    table.pack(fill="both", expand=True, padx=6, pady=6)

    event_queue: "queue.Queue[dict]" = queue.Queue()
    watcher = JournalWatcher(journal_dir=journal_dir or default_journal_dir())
    watcher.start(event_queue.put)

    scorer = create_scorer()
    current_system: Optional[str] = None

    def handle_event(event: dict) -> None:
        nonlocal current_system
        event_name = event.get("event")

        if event_name in ("FSDJump", "Location"):
            star_system = event.get("StarSystem")
            if star_system and star_system != current_system:
                current_system = star_system
                table.clear()
                status_var.set(f"System: {star_system}")
            return

        if event_name == "Scan":
            rows: list[RingRow] = build_ring_rows(event, scorer)
            now = time.time()
            for row in rows:
                table.upsert(row, now)

    def poll_queue() -> None:
        try:
            while True:
                event = event_queue.get_nowait()
                handle_event(event)
        except queue.Empty:
            pass
        root.after(_QUEUE_POLL_MS, poll_queue)

    def purge_tick() -> None:
        table.purge_expired(time.time())
        root.after(_PURGE_TICK_MS, purge_tick)

    def on_close() -> None:
        watcher.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(_QUEUE_POLL_MS, poll_queue)
    root.after(_PURGE_TICK_MS, purge_tick)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Elite Dangerous ring density monitor")
    parser.add_argument(
        "--journal-dir",
        default=None,
        help=(
            "Override the journal directory. Precedence: this flag > "
            "%%APPDATA%%\\RingDensityMonitor\\config.json's journal_dir > "
            "the standard Saved Games location."
        ),
    )
    args = parser.parse_args()
    run(journal_dir=resolve_journal_dir(args.journal_dir))


if __name__ == "__main__":
    main()
