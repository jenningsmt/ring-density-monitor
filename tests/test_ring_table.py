"""Tests for the Tkinter ring table widget's row lifecycle logic.

Uses a real (withdrawn) Tk root -- these tests need a display session, same
as the app itself, but never show a visible window.
"""

from __future__ import annotations

import tkinter as tk

import pytest

from app.ring_table import DEFAULT_BLACK_TIER_TTL_SECONDS, RingTable
from core.ring_display import RingRow


def _row(key: str, tier: str, delta: float | None) -> RingRow:
    return RingRow(
        ring_key=key,
        body_designation="5",
        body_type_label="Icy World",
        ring_id=key[-1],
        ring_type="Icy",
        density_label="12.3 t/km²",
        delta_pct=delta,
        tier=tier,
    )


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"No display available for Tkinter: {exc}")
    r.withdraw()
    yield r
    r.destroy()


class TestRingTableUpsert:
    def test_new_row_creates_labels_with_expected_text(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:A", "green", 92.0), now=1000.0)

        row = table._rows["5:A"]
        texts = [label.cget("text") for label in row.labels]
        assert texts == ["5 / Icy World", "A", "Icy", "12.3 t/km²", "+92%"]

    def test_green_tier_is_bold_and_colored(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:A", "green", 92.0), now=1000.0)
        delta_label = table._rows["5:A"].labels[4]
        assert delta_label.cget("fg") == "#1a7f37"
        assert "bold" in delta_label.cget("font")

    def test_red_tier_is_bold_and_colored(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:B", "red", 140.0), now=1000.0)
        delta_label = table._rows["5:B"].labels[4]
        assert delta_label.cget("fg") == "#c62828"
        assert "bold" in delta_label.cget("font")

    def test_black_tier_is_plain(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:C", "black", 10.0), now=1000.0)
        delta_label = table._rows["5:C"].labels[4]
        assert delta_label.cget("fg") == "#000000"
        assert "bold" not in delta_label.cget("font")

    def test_upsert_same_key_updates_in_place(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:A", "black", 10.0), now=1000.0)
        table.upsert(_row("5:A", "red", 150.0), now=1005.0)

        assert len(table._rows) == 1
        row = table._rows["5:A"]
        assert row.tier == "red"
        assert row.inserted_at == 1005.0


class TestRingTablePurgeExpired:
    def test_black_row_survives_before_ttl(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:A", "black", 10.0), now=1000.0)
        table.purge_expired(now=1000.0 + 59.0, ttl=60.0)
        assert "5:A" in table._rows

    def test_black_row_removed_after_ttl(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:A", "black", 10.0), now=1000.0)
        table.purge_expired(now=1000.0 + 61.0, ttl=60.0)
        assert "5:A" not in table._rows

    def test_default_ttl_is_180_seconds(self, root: tk.Tk) -> None:
        assert DEFAULT_BLACK_TIER_TTL_SECONDS == 180.0

        table = RingTable(root)
        table.upsert(_row("5:A", "black", 10.0), now=1000.0)

        table.purge_expired(now=1000.0 + 179.0)  # ttl omitted -> uses default
        assert "5:A" in table._rows

        table.purge_expired(now=1000.0 + 181.0)
        assert "5:A" not in table._rows

    def test_green_and_red_rows_never_expire_by_age(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:A", "green", 92.0), now=1000.0)
        table.upsert(_row("5:B", "red", 150.0), now=1000.0)
        table.purge_expired(now=1000.0 + 10_000.0, ttl=60.0)
        assert "5:A" in table._rows
        assert "5:B" in table._rows

    def test_refreshed_black_row_is_not_purged(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:A", "black", 10.0), now=1000.0)
        table.upsert(_row("5:A", "black", 10.0), now=1050.0)  # re-touched
        table.purge_expired(now=1050.0 + 59.0, ttl=60.0)
        assert "5:A" in table._rows


class TestRingTableClear:
    def test_clear_removes_all_rows_regardless_of_tier(self, root: tk.Tk) -> None:
        table = RingTable(root)
        table.upsert(_row("5:A", "black", 10.0), now=1000.0)
        table.upsert(_row("5:B", "red", 150.0), now=1000.0)
        table.clear()
        assert table._rows == {}
        assert table._order == []
