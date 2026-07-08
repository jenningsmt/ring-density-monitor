"""Tests for persisted app settings (app/config.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import (
    DEFAULT_CONFIG,
    config_path,
    load_config,
    resolve_journal_dir,
    save_config,
)


@pytest.fixture
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point config_path() at a throwaway directory so tests never touch the
    real user's %APPDATA%\\RingDensityMonitor\\config.json."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


class TestConfigPath:
    def test_lives_under_appdata_app_name(self, isolated_appdata: Path) -> None:
        path = config_path()
        assert path == isolated_appdata / "RingDensityMonitor" / "config.json"


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, isolated_appdata: Path) -> None:
        assert load_config() == DEFAULT_CONFIG

    def test_round_trip_save_then_load(self, isolated_appdata: Path) -> None:
        save_config({"journal_dir": "D:\\Custom\\Journal"})
        assert load_config()["journal_dir"] == "D:\\Custom\\Journal"

    def test_corrupt_json_falls_back_to_defaults(self, isolated_appdata: Path) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")

        assert load_config() == DEFAULT_CONFIG

    def test_non_dict_json_falls_back_to_defaults(self, isolated_appdata: Path) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]", encoding="utf-8")

        assert load_config() == DEFAULT_CONFIG


class TestResolveJournalDir:
    def test_cli_override_wins_over_everything(self, isolated_appdata: Path) -> None:
        save_config({"journal_dir": "D:\\Configured"})
        assert resolve_journal_dir("D:\\FromCli") == "D:\\FromCli"

    def test_config_used_when_no_cli_override(self, isolated_appdata: Path) -> None:
        save_config({"journal_dir": "D:\\Configured"})
        assert resolve_journal_dir(None) == "D:\\Configured"

    def test_none_when_neither_set(self, isolated_appdata: Path) -> None:
        assert resolve_journal_dir(None) is None

    def test_empty_string_cli_override_falls_through_to_config(self, isolated_appdata: Path) -> None:
        save_config({"journal_dir": "D:\\Configured"})
        assert resolve_journal_dir("") == "D:\\Configured"
