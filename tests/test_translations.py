"""Tests for strings.json and translations/en.json — Phase 17 options structure."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

# Paths relative to the project root
_ROOT = Path(__file__).parent.parent
_STRINGS = _ROOT / "custom_components" / "shop2parcel" / "strings.json"
_EN = _ROOT / "custom_components" / "shop2parcel" / "translations" / "en.json"


@pytest.fixture(scope="module")
def strings() -> dict:
    """Load strings.json as a dict (module-scoped for performance)."""
    with open(_STRINGS) as f:
        return json.load(f)


def test_strings_init_menu_options(strings):
    """Test 1: strings.json has init.menu_options with Settings and custom_fields."""
    opts = strings["options"]["step"]["init"]["menu_options"]
    assert opts["settings"] == "Settings"
    assert opts["custom_fields"] == "Manage custom extraction fields"


def test_strings_settings_step(strings):
    """Test 2: settings step has title and description containing {locked_fields}."""
    settings_step = strings["options"]["step"]["settings"]
    assert settings_step["title"] == "Shop2Parcel Settings"
    assert "{locked_fields}" in settings_step["description"]


def test_strings_settings_data_keys(strings):
    """Test 3: settings.data has all nine field labels."""
    data = strings["options"]["step"]["settings"]["data"]
    expected_keys = {
        "poll_interval",
        "gmail_query",
        "imap_search",
        "rescan_window_days",
        "debug_mode",
        "ollama_url",
        "ollama_model",
        "ollama_timeout",
        "queue_maxlen",
    }
    actual_keys = set(data.keys())
    assert actual_keys == expected_keys, (
        f"settings.data keys mismatch: expected {sorted(expected_keys)}, got {sorted(actual_keys)}"
    )


def test_strings_error_keys(strings):
    """Test 4: options.error has ollama_cannot_connect and ollama_model_not_found."""
    errors = strings["options"]["error"]
    assert "ollama_cannot_connect" in errors
    assert len(errors["ollama_cannot_connect"]) > 0
    assert "ollama_model_not_found" in errors
    assert len(errors["ollama_model_not_found"]) > 0
    # The model_not_found value must contain the interpolation placeholder
    assert "{missing_model}" in errors["ollama_model_not_found"]


def test_strings_abort_not_implemented(strings):
    """Test 5: options.abort.not_implemented exists and is non-empty."""
    abort = strings["options"]["abort"]
    assert "not_implemented" in abort
    assert len(abort["not_implemented"]) > 0


def test_strings_en_json_identical():
    """Test 6: translations/en.json is byte-identical to strings.json."""
    result = subprocess.run(
        ["cmp", "-s", str(_STRINGS), str(_EN)],
        capture_output=True,
    )
    assert result.returncode == 0, (
        "strings.json and translations/en.json are NOT identical — "
        "they must be kept in sync as a single atomic edit"
    )


def test_strings_init_only_menu_options(strings):
    """Test 7: options.step.init has ONLY menu_options (no leftover title/data/data_description)."""
    init_step = strings["options"]["step"]["init"]
    assert set(init_step.keys()) == {"menu_options"}, (
        f"options.step.init must have ONLY 'menu_options', got: {sorted(init_step.keys())}"
    )
