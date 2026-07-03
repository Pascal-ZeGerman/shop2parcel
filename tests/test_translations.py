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
    """Test 3: settings.data has all ten field labels."""
    data = strings["options"]["step"]["settings"]["data"]
    expected_keys = {
        "poll_interval",
        "gmail_query",
        "imap_search",
        "imap_verify_tls",
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


def test_strings_abort_not_implemented_removed(strings):
    """Test 5 (updated by Plan 04): options.abort.not_implemented is no longer present."""
    # Plan 03 added this key for the stub; Plan 04 replaces the stub with real CRUD,
    # so the abort key is no longer reachable and must be removed.
    abort = strings["options"].get("abort", {})
    assert "not_implemented" not in abort, (
        "options.abort.not_implemented must be removed — the stub was replaced by real CRUD"
    )


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


# ---------------------------------------------------------------------------
# Plan 04 translation tests: FLD-01/FLD-02 custom-fields step blocks + error keys
# ---------------------------------------------------------------------------


def test_custom_fields_menu_keys(strings):
    """Plan 04 Test 1 (RED): custom_fields_menu.menu_options has add + remove keys."""
    menu_opts = strings["options"]["step"]["custom_fields"]["menu_options"]
    assert "add_custom_field" in menu_opts
    assert menu_opts["add_custom_field"] == "Add custom field"
    assert "remove_custom_field" in menu_opts
    assert menu_opts["remove_custom_field"] == "Remove custom field"


def test_custom_fields_menu_description_placeholder(strings):
    """Plan 04 Test 2: custom_fields_menu.description contains {current_fields} placeholder."""
    desc = strings["options"]["step"]["custom_fields"]["description"]
    assert "{current_fields}" in desc, (
        f"custom_fields_menu.description must contain '{{current_fields}}', got: {desc!r}"
    )


def test_add_custom_field_data_keys(strings):
    """Plan 04 Test 3: add_custom_field.data has field_name and field_description with descriptions."""
    step = strings["options"]["step"]["add_custom_field"]
    assert "field_name" in step["data"]
    assert len(step["data"]["field_name"]) > 0
    assert "field_description" in step["data"]
    assert len(step["data"]["field_description"]) > 0
    assert "field_name" in step["data_description"]
    assert len(step["data_description"]["field_name"]) > 0
    assert "field_description" in step["data_description"]
    assert len(step["data_description"]["field_description"]) > 0


def test_remove_custom_field_data_key(strings):
    """Plan 04 Test 4: remove_custom_field.data.field_name is present and non-empty."""
    step = strings["options"]["step"]["remove_custom_field"]
    assert "field_name" in step["data"]
    assert len(step["data"]["field_name"]) > 0


def test_new_error_keys_present(strings):
    """Plan 04 Test 5: options.error has invalid_field_name + locked_field_collision."""
    errors = strings["options"]["error"]
    assert "invalid_field_name" in errors
    assert len(errors["invalid_field_name"]) > 0
    assert "locked_field_collision" in errors
    assert len(errors["locked_field_collision"]) > 0
    # WR-01: control-character rejection for the IMAP search string.
    assert "invalid_imap_search" in errors
    assert len(errors["invalid_imap_search"]) > 0
    # Plan 03 keys + Plan 04 keys + WR-01 key — 5 total
    assert len(errors) == 5, f"Expected 5 error keys total, got {sorted(errors.keys())}"


def test_not_implemented_abort_absent(strings):
    """Plan 04 Test 6: options.abort.not_implemented is absent (stub removed)."""
    abort = strings["options"].get("abort", {})
    assert "not_implemented" not in abort


def test_en_json_identical_to_strings():
    """Plan 04 Test 7 (sync rule): translations/en.json is byte-identical to strings.json."""
    result = subprocess.run(
        ["cmp", "-s", str(_STRINGS), str(_EN)],
        capture_output=True,
    )
    assert result.returncode == 0, (
        "strings.json and translations/en.json are NOT identical after Plan 04 edit"
    )


def test_plan03_keys_preserved(strings):
    """Plan 04 Test 8 (regression): Plan 03 keys still present after Plan 04 edit."""
    opts = strings["options"]["step"]["init"]["menu_options"]
    assert "settings" in opts
    assert "custom_fields" in opts
    settings = strings["options"]["step"]["settings"]
    assert settings["title"] == "Shop2Parcel Settings"
    errors = strings["options"]["error"]
    assert "ollama_cannot_connect" in errors
    assert "ollama_model_not_found" in errors
