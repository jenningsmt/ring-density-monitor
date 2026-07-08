"""Tkinter widget: a live-updating table of detected rings.

Built from a grid of individual Labels (not ttk.Treeview) specifically so
color/weight can be applied to a single cell — the delta-from-galactic-norm
column — while every other cell stays plain text. Treeview only supports
whole-row tagging in the default theme, which doesn't fit that requirement.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from typing import Optional

from core.ring_display import RingRow, format_delta

_FONT = ("Consolas", 15)
_HEADER_FONT = ("Consolas", 15, "bold")
_BOLD_FONT = ("Consolas", 15, "bold")

_TIER_STYLE = {
    "black": {"fg": "#000000", "font": _FONT},
    "green": {"fg": "#1a7f37", "font": _BOLD_FONT},
    "red": {"fg": "#c62828", "font": _BOLD_FONT},
    "unknown": {"fg": "#000000", "font": _FONT},
}

_COLUMNS = ("Body", "Ring", "Type", "Density", "Δ Galactic")

# Auto-hide window for black-tier (not-worth-scanning) rows. Green/red rows
# are never removed by age -- only by clear() on system departure.
DEFAULT_BLACK_TIER_TTL_SECONDS = 180.0


@dataclass
class _Row:
    order_key: str
    labels: tuple  # (body, ring, type, density, delta) Label widgets
    tier: str
    inserted_at: float


class RingTable(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self._rows: dict[str, _Row] = {}
        self._order: list[str] = []
        self._build_header()

    def _build_header(self) -> None:
        for col, title in enumerate(_COLUMNS):
            label = tk.Label(self, text=title, font=_HEADER_FONT, anchor="w")
            label.grid(row=0, column=col, sticky="w", padx=(4, 12), pady=(2, 4))

    def upsert(self, row: RingRow, now: float) -> None:
        existing = self._rows.get(row.ring_key)
        if existing is None:
            labels = (
                tk.Label(self, font=_FONT, anchor="w"),
                tk.Label(self, font=_FONT, anchor="w"),
                tk.Label(self, font=_FONT, anchor="w"),
                tk.Label(self, font=_FONT, anchor="w"),
                tk.Label(self, font=_FONT, anchor="w"),
            )
            self._rows[row.ring_key] = _Row(
                order_key=row.ring_key, labels=labels, tier=row.tier, inserted_at=now
            )
            self._order.append(row.ring_key)
        else:
            labels = existing.labels
            existing.tier = row.tier
            existing.inserted_at = now

        body_text = f"{row.body_designation} / {row.body_type_label}"
        texts = (body_text, row.ring_id, row.ring_type, row.density_label, format_delta(row.delta_pct))
        for label, text in zip(labels, texts):
            label.configure(text=text)

        delta_style = _TIER_STYLE.get(row.tier, _TIER_STYLE["black"])
        labels[4].configure(fg=delta_style["fg"], font=delta_style["font"])

        self._regrid()

    def purge_expired(self, now: float, ttl: float = DEFAULT_BLACK_TIER_TTL_SECONDS) -> None:
        expired = [
            key
            for key, row in self._rows.items()
            if row.tier == "black" and (now - row.inserted_at) >= ttl
        ]
        if not expired:
            return
        for key in expired:
            self._destroy_row(key)
        self._regrid()

    def clear(self) -> None:
        for key in list(self._rows.keys()):
            self._destroy_row(key)
        self._order.clear()

    def _destroy_row(self, key: str) -> None:
        row = self._rows.pop(key, None)
        if row is None:
            return
        for label in row.labels:
            label.destroy()
        if key in self._order:
            self._order.remove(key)

    def _regrid(self) -> None:
        for grid_row, key in enumerate(self._order, start=1):
            row = self._rows[key]
            for col, label in enumerate(row.labels):
                label.grid(row=grid_row, column=col, sticky="w", padx=(4, 12), pady=1)
