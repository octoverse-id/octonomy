"""Registry-driven isolation sweep (issue #44, acceptance criterion #1).

For every registered v2 read endpoint, seed a merchant_a row and assert a
merchant_b caller can never see it, while merchant_a can. A new read endpoint
with no fixture spec fails ``test_every_v2_read_endpoint_has_a_fixture_spec``.
"""

from __future__ import annotations

import pytest

from tests.isolation.registry import (
    APP,
    FIXTURE_SPECS,
    collect_strings,
    v2_read_endpoint_names,
)

pytestmark = pytest.mark.django_db


def _with_application(path: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}application_id={APP}"


def test_every_v2_read_endpoint_has_a_fixture_spec():
    registered = v2_read_endpoint_names()
    mapped = set(FIXTURE_SPECS)

    unmapped = registered - mapped
    assert not unmapped, (
        "New v2 read endpoint(s) without an isolation fixture spec: "
        f"{sorted(unmapped)}. Add a seed function to tests/isolation/registry.py "
        "FIXTURE_SPECS so the isolation sweep covers it (issue #44)."
    )

    stale = mapped - registered
    assert not stale, (
        f"Isolation fixture spec(s) for endpoints that no longer exist: {sorted(stale)}. "
        "Remove them from FIXTURE_SPECS."
    )


@pytest.mark.parametrize("name", sorted(FIXTURE_SPECS))
def test_merchant_b_cannot_see_merchant_a_rows(name, merchant_a_client, merchant_b_client):
    scenario = FIXTURE_SPECS[name]()
    url = _with_application(scenario.path)

    intruder = merchant_b_client.get(url)
    assert intruder.status_code in scenario.b_status, (
        name,
        intruder.status_code,
        intruder.data,
    )
    leaked = scenario.forbidden_ids & collect_strings(intruder.json())
    assert not leaked, f"{name} leaked merchant_a rows to merchant_b: {sorted(leaked)}"


@pytest.mark.parametrize("name", sorted(FIXTURE_SPECS))
def test_merchant_a_sees_its_own_rows(name, merchant_a_client):
    # Non-vacuous guard: an endpoint that 404s for everyone would pass the leak
    # assertion above for the wrong reason. The owner must actually see its row.
    scenario = FIXTURE_SPECS[name]()
    owner = merchant_a_client.get(_with_application(scenario.path))
    assert owner.status_code == 200, (name, owner.status_code, owner.data)
    assert scenario.positive_id in collect_strings(owner.json()), (
        f"{name} did not return merchant_a's own row to merchant_a; "
        "the isolation scenario is not exercising a real read path."
    )
