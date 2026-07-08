"""Tests for the standalone journal watcher."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from core.journal_watcher import JournalWatcher, parse_journal_line


class TestParseJournalLine:
    def test_valid_event(self) -> None:
        line = json.dumps({"event": "Scan", "BodyID": 1})
        assert parse_journal_line(line) == {"event": "Scan", "BodyID": 1}

    def test_blank_line(self) -> None:
        assert parse_journal_line("   \n") is None

    def test_malformed_json(self) -> None:
        assert parse_journal_line("{not json") is None

    def test_missing_event_field(self) -> None:
        assert parse_journal_line(json.dumps({"BodyID": 1})) is None

    def test_non_object_json(self) -> None:
        assert parse_journal_line(json.dumps([1, 2, 3])) is None


def _wait_for(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestJournalWatcherTailing:
    def test_ignores_backlog_then_picks_up_new_lines(self, tmp_path: Path) -> None:
        journal_dir = tmp_path
        journal_file = journal_dir / "Journal.2026-01-01T000000.01.log"
        backlog_event = {"event": "Scan", "BodyID": 1, "tag": "backlog"}
        journal_file.write_text(json.dumps(backlog_event) + "\n", encoding="utf-8")

        seen: list[dict] = []
        lock = threading.Lock()

        def on_event(event: dict) -> None:
            with lock:
                seen.append(event)

        watcher = JournalWatcher(journal_dir=journal_dir, poll_interval=0.05)
        watcher.start(on_event)
        try:
            # Give the watcher a moment to attach and seek past the backlog.
            time.sleep(0.2)
            with journal_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "Scan", "BodyID": 2, "tag": "live"}) + "\n")

            assert _wait_for(lambda: len(seen) >= 1)
        finally:
            watcher.stop()

        with lock:
            tags = [event.get("tag") for event in seen]
        assert "backlog" not in tags
        assert "live" in tags

    def test_ignores_irrelevant_event_types(self, tmp_path: Path) -> None:
        journal_dir = tmp_path
        journal_file = journal_dir / "Journal.2026-01-01T000000.01.log"
        journal_file.write_text("", encoding="utf-8")

        seen: list[dict] = []
        lock = threading.Lock()

        def on_event(event: dict) -> None:
            with lock:
                seen.append(event)

        watcher = JournalWatcher(journal_dir=journal_dir, poll_interval=0.05)
        watcher.start(on_event)
        try:
            time.sleep(0.2)
            with journal_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "ReceiveText"}) + "\n")
                handle.write(json.dumps({"event": "FSDJump", "StarSystem": "Sol"}) + "\n")

            assert _wait_for(lambda: len(seen) >= 1)
            time.sleep(0.2)  # let a possible (incorrect) ReceiveText delivery show up
        finally:
            watcher.stop()

        with lock:
            event_names = [event.get("event") for event in seen]
        assert event_names == ["FSDJump"]

    def test_rotation_to_newer_file_reads_from_start(self, tmp_path: Path) -> None:
        journal_dir = tmp_path
        first_file = journal_dir / "Journal.2026-01-01T000000.01.log"
        first_file.write_text(json.dumps({"event": "Scan", "BodyID": 1}) + "\n", encoding="utf-8")

        seen: list[dict] = []
        lock = threading.Lock()

        def on_event(event: dict) -> None:
            with lock:
                seen.append(event)

        watcher = JournalWatcher(journal_dir=journal_dir, poll_interval=0.05)
        watcher.start(on_event)
        try:
            time.sleep(0.2)

            second_file = journal_dir / "Journal.2026-01-01T010000.02.log"
            second_event = {"event": "Scan", "BodyID": 99, "tag": "second-file"}
            second_file.write_text(json.dumps(second_event) + "\n", encoding="utf-8")

            assert _wait_for(lambda: len(seen) >= 1)
        finally:
            watcher.stop()

        with lock:
            tags = [event.get("tag") for event in seen]
        assert "second-file" in tags

    def test_missing_journal_dir_does_not_crash(self, tmp_path: Path) -> None:
        watcher = JournalWatcher(journal_dir=tmp_path / "does-not-exist", poll_interval=0.05)
        watcher.start(lambda event: None)
        time.sleep(0.2)
        watcher.stop()  # no assertion needed: absence of a crash is the test
