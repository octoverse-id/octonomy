"""Flag-ordering chaos (issue #44, acceptance criterion #3).

The invariant #44 asks for: flipping flags in the permitted orders under traffic
must never make an *accepted* write unreadable. Pre-S7 there is exactly one
namespace flag — ``NAMESPACE_WRITE_ENABLED`` — the kill switch that gates
namespaced writes. So the chaos exercised here toggles that switch while a
merchant streams writes and reads, and asserts every write that was accepted
(``201``) stays readable across every later flag state.

When S7 (#45) lands the full flag set (``NAMESPACE_V2_API_ENABLED``,
``NAMESPACE_READ_ENABLED``, ``NAMESPACE_AUTH_ENFORCED``, …) and their
system-check dependency contract, ``FLAG_SEQUENCES`` below extends to the
permitted rollout/rollback ladders; the read-durability assertion is unchanged.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from tests.isolation.registry import APP

pytestmark = pytest.mark.django_db


def _write_tag(client, slug: str):
    return client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": slug, "slug": slug, "type": "label"},
        format="json",
    )


def _detail(client, tag_id: str):
    return client.get(f"/api/v2/tags/{tag_id}?application_id={APP}")


def _list_ids(client) -> set[str]:
    response = client.get(f"/api/v2/tags?application_id={APP}")
    assert response.status_code == 200, response.data
    return {item["id"] for item in response.json()["data"]}


# Rollout (enable) and rollback (disable) of the write kill switch, interleaved.
FLAG_SEQUENCES = [
    pytest.param([True, False, True, False], id="enable-disable-cycle"),
    pytest.param([True, True, False, False, True], id="rollout-then-rollback"),
    pytest.param([False, True, False, True], id="rollback-first"),
]


@pytest.mark.parametrize("write_states", FLAG_SEQUENCES)
def test_flag_toggle_never_makes_an_accepted_write_unreadable(merchant_a_client, write_states):
    accepted: list[str] = []

    for step, write_enabled in enumerate(write_states):
        with override_settings(NAMESPACE_WRITE_ENABLED=write_enabled):
            response = _write_tag(merchant_a_client, f"chaos-{step}")
            if response.status_code == 201:
                accepted.append(response.json()["data"]["id"])
            else:
                # A refused write is fine — it was never accepted. It must not be
                # anything other than the kill switch's structured refusal.
                assert response.status_code == 403, (step, response.data)
                assert response.data["error"]["code"] == "namespaced_writes_disabled"

            # Whatever the current flag state, every previously accepted write is
            # still readable — by id and in the collection.
            for tag_id in accepted:
                got = _detail(merchant_a_client, tag_id)
                assert got.status_code == 200, (step, write_enabled, tag_id, got.status_code)
            assert set(accepted) <= _list_ids(merchant_a_client), (step, write_enabled)


def test_kill_switch_refuses_new_namespaced_writes_without_hiding_prior_ones(merchant_a_client):
    with override_settings(NAMESPACE_WRITE_ENABLED=True):
        first = _write_tag(merchant_a_client, "before-killswitch")
        assert first.status_code == 201, first.data
        tag_id = first.json()["data"]["id"]

    with override_settings(NAMESPACE_WRITE_ENABLED=False):
        refused = _write_tag(merchant_a_client, "after-killswitch")
        assert refused.status_code == 403
        assert refused.data["error"]["code"] == "namespaced_writes_disabled"
        # The kill switch stops new namespaced writes; it must not retract an
        # already-accepted one.
        assert _detail(merchant_a_client, tag_id).status_code == 200
        assert tag_id in _list_ids(merchant_a_client)


def test_global_writes_are_accepted_and_readable_while_namespaced_writes_are_off(api_client):
    # "Writes stay global" on rollback: with the kill switch off, a global write
    # is still accepted and readable — the rollback ladder never blocks v1/global.
    with override_settings(NAMESPACE_WRITE_ENABLED=False):
        response = api_client.post(
            "/api/v2/tags",
            {"application_id": APP, "name": "Global", "slug": "global-during-off", "type": "label"},
            format="json",
        )
        assert response.status_code == 201, response.data
        tag_id = response.json()["data"]["id"]
        assert api_client.get(f"/api/v2/tags/{tag_id}?application_id={APP}").status_code == 200
