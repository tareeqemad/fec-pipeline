"""Patch a name in every fec.geocoding module that holds it."""
import sys


def point(lat, lng, country="US", source=None, matched_address=None):
    result = {"lat": lat, "lng": lng, "country": country,
              "matched_address": matched_address or {}}
    if source is not None:
        result["source"] = source
    return result


def patch_geo(monkeypatch, name, value):
    """Set name wherever a geocoding module looks it up."""
    modules = [m for key, m in sys.modules.items() if key.startswith("fec.geocoding") and hasattr(m, name)]
    assert modules, f"no fec.geocoding module has {name}"
    for module in modules:
        monkeypatch.setattr(module, name, value)
