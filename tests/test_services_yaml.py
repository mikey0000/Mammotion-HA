"""Validate services.yaml against Home Assistant selector constraints.

HA enforces specific rules on number selector fields (e.g. step >= 0.001) that
only surface at runtime when the integration loads.  This test catches those
violations at commit time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SERVICES_YAML = Path(__file__).parent.parent / "custom_components" / "mammotion" / "services.yaml"

# Constraints mirrored from homeassistant/helpers/selector.py
NUMBER_STEP_MIN = 1e-3


def _iter_number_selectors(data: dict, path: str = ""):
    """Yield (path, selector_dict) for every number selector in *data*."""
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        current = f"{path}.{key}" if path else key
        if isinstance(value, dict):
            if "number" in value:
                yield current, value["number"]
            else:
                yield from _iter_number_selectors(value, current)


def _collect_field_selectors(services: dict):
    """Return list of (path, selector_dict) for all field selectors in services.yaml."""
    results = []
    for svc_name, svc_data in services.items():
        if not isinstance(svc_data, dict):
            continue
        fields = svc_data.get("fields") or {}
        for field_name, field_data in fields.items():
            if not isinstance(field_data, dict):
                continue
            selector = field_data.get("selector")
            if selector and isinstance(selector, dict) and "number" in selector:
                num_cfg = selector["number"]
                if isinstance(num_cfg, dict):  # selector: {number:} yields None
                    path = f"{svc_name}.fields.{field_name}.selector"
                    results.append((path, num_cfg))
    return results


@pytest.fixture(scope="module")
def services():
    """Load and return the parsed services.yaml."""
    with SERVICES_YAML.open() as f:
        return yaml.safe_load(f)


def test_services_yaml_is_valid_yaml(services):
    """services.yaml parses without error and produces a dict."""
    assert isinstance(services, dict), "services.yaml must be a YAML mapping"


def test_number_selector_step_minimum(services):
    """Every number selector step must be >= 0.001 (HA constraint)."""
    violations = []
    for path, num_sel in _collect_field_selectors(services):
        step = num_sel.get("step")
        if step is not None and step < NUMBER_STEP_MIN:
            violations.append(f"  {path}.number.step={step!r} (min {NUMBER_STEP_MIN})")
    assert not violations, (
        "services.yaml contains number selectors with step < 0.001:\n"
        + "\n".join(violations)
    )


def test_number_selector_min_max_order(services):
    """number selector min must be <= max."""
    violations = []
    for path, num_sel in _collect_field_selectors(services):
        lo = num_sel.get("min")
        hi = num_sel.get("max")
        if lo is not None and hi is not None and lo > hi:
            violations.append(f"  {path}: min={lo} > max={hi}")
    assert not violations, (
        "services.yaml contains number selectors where min > max:\n"
        + "\n".join(violations)
    )


def test_required_field_has_selector_or_example(services):
    """Every required field should have either a selector or an example."""
    violations = []
    for svc_name, svc_data in services.items():
        if not isinstance(svc_data, dict):
            continue
        for field_name, field_data in (svc_data.get("fields") or {}).items():
            if not isinstance(field_data, dict):
                continue
            if field_data.get("required") and not field_data.get("selector") and not field_data.get("example"):
                violations.append(f"  {svc_name}.fields.{field_name}")
    assert not violations, (
        "Required fields missing selector and example:\n" + "\n".join(violations)
    )
