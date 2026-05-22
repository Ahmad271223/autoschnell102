"""Regression test for the dynamic make/model JSON lookup.

Ensures `mobile_service` correctly resolves vehicles to mobile.de's
internal numeric IDs sourced from `mobile_makes_models.json`.
Run with: pytest /app/backend/tests/test_mobile_service_makes.py
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mobile_service import (  # noqa: E402
    DEFAULT_RULES,
    _MAKES_INDEX,
    _resolve_make,
    _resolve_model,
    build_search_url,
)


def test_catalogue_loaded():
    assert len(_MAKES_INDEX) >= 170, "JSON catalogue should provide ≥170 makes"


def test_resolve_known_makes():
    cases = [
        # (make_label, model_label, expected_make_id, expected_model_id)
        ("BMW", "318", "3500", "9"),
        ("BMW", "325", "3500", "13"),
        ("Volkswagen", "Golf", "25200", "14"),
        ("Volkswagen", "Passat Variant", "25200", "63"),
        ("Mercedes-Benz", "C 200", "17200", "18"),
        ("Citroën", "C3", "5900", "11"),
        ("Nissan", "Qashqai", "18700", "47"),
        ("Skoda", "Octavia", "22900", "10"),
        ("Kia", "Sorento", "13200", "24"),
        # Kleinanzeigen often returns the catalogue name without paren-suffix
        # (e.g. "Aygo" instead of "Aygo (X)"). Both forms must resolve.
        ("Toyota", "Aygo", "24100", "5"),
        ("Toyota", "Aygo (X)", "24100", "5"),
    ]
    for make_label, model_label, exp_mk, exp_md in cases:
        v = {"make_label": make_label, "model_label": model_label}
        mk_id, entry = _resolve_make(v)
        md_id = _resolve_model(entry, v)
        assert mk_id == exp_mk, f"{make_label}: make {mk_id} != {exp_mk}"
        assert md_id == exp_md, f"{make_label} {model_label}: model {md_id} != {exp_md}"


def test_alias_vw_routes_to_volkswagen():
    """API key 'VW' must alias to the catalogue 'Volkswagen' entry."""
    v = {"make": "VW", "make_label": None, "model_label": "Golf"}
    mk_id, entry = _resolve_make(v)
    assert mk_id == "25200"
    assert _resolve_model(entry, v) == "14"


def test_unknown_make_returns_no_filter():
    """Unknown makes should produce no `ms=` segment (better than wrong)."""
    v = {"make": "MARSIANS", "make_label": "Marsians", "model_label": "X"}
    url = build_search_url(v, DEFAULT_RULES)
    assert "ms=" not in url


def test_compact_url_contains_make_and_model():
    v = {
        "make_label": "BMW",
        "model_label": "325",
        "first_registration": "08/2015",
        "mileage": 100000,
        "power_kw": 100,
        "power_ps": 136,
    }
    url = build_search_url(v, DEFAULT_RULES)
    ms = re.search(r"ms=([^&]+)", url)
    assert ms, "URL must include ms= segment"
    assert unquote(ms.group(1)).startswith("3500;13;"), f"unexpected ms: {ms.group(1)}"
