"""Mnemosyne album foundation — the deterministic layout validator (pure unit).

No DB, no network: these pin the one invariant the audit (§11.4) makes deterministic
code responsible for — an album layout must never silently omit, duplicate, or misassign
a photo. They drive ``validate_core`` directly with an explicit eligible set, plus the
mock ALBUMS adapter and the registry seam.
"""

import pytest

from app import albums
from app.providers import mocks, registry
from app.providers.contracts import Capability, ResultStatus, ReviewRequirement

pytestmark = pytest.mark.unit

ELIGIBLE = {1, 2, 3, 4}


def _codes(v):
    return sorted(i.code for i in v.issues)


def _place(*specs):
    """specs are (asset_id, spread, slot) tuples -> placement dicts."""
    return [{"asset_id": a, "spread": s, "slot": sl} for a, s, sl in specs]


# ── happy path + omission surfacing ────────────────────────────────────────────


def test_valid_layout_is_ok_and_reports_omitted():
    v = albums.validate_core(ELIGIBLE, _place((1, 0, 0), (2, 0, 1), (3, 1, 0)))
    assert v.ok
    assert v.issues == ()
    assert v.placed == (1, 2, 3)
    # asset 4 was eligible but not placed: surfaced, never silent — and not a hard issue.
    assert v.omitted == (4,)


def test_full_layout_has_no_omissions():
    v = albums.validate_core(ELIGIBLE, _place((1, 0, 0), (2, 0, 1), (3, 1, 0), (4, 1, 1)))
    assert v.ok
    assert v.omitted == ()


def test_empty_layout_is_ok_but_omits_everything():
    # validate_core does not treat empty as a hard issue; save_draft is what refuses it.
    v = albums.validate_core(ELIGIBLE, [])
    assert v.ok
    assert v.omitted == (1, 2, 3, 4)
    assert v.placed == ()


# ── the three forbidden states ─────────────────────────────────────────────────


def test_duplicate_photo_is_a_hard_issue():
    v = albums.validate_core(ELIGIBLE, _place((1, 0, 0), (1, 1, 0)))
    assert not v.ok
    assert "duplicate" in _codes(v)
    dup = next(i for i in v.issues if i.code == "duplicate")
    assert dup.asset_id == 1 and "2 times" in dup.detail


def test_duplicate_reported_once_even_when_repeated_thrice():
    v = albums.validate_core(ELIGIBLE, _place((1, 0, 0), (1, 1, 0), (1, 2, 0)))
    dups = [i for i in v.issues if i.code == "duplicate"]
    assert len(dups) == 1 and dups[0].asset_id == 1


def test_foreign_asset_is_a_hard_issue():
    # 99 is not an eligible photo of this gallery (wrong gallery / video / not ready).
    v = albums.validate_core(ELIGIBLE, _place((1, 0, 0), (99, 0, 1)))
    assert not v.ok
    foreign = [i for i in v.issues if i.code == "foreign_asset"]
    assert len(foreign) == 1 and foreign[0].asset_id == 99


def test_slot_collision_is_a_hard_issue():
    v = albums.validate_core(ELIGIBLE, _place((1, 0, 0), (2, 0, 0)))
    assert not v.ok
    coll = next(i for i in v.issues if i.code == "slot_collision")
    assert coll.spread == 0 and coll.slot == 0 and coll.asset_id == 2


def test_same_asset_different_slots_is_duplicate_not_collision():
    # Distinct slots so no collision, but the same asset twice is still a duplicate.
    v = albums.validate_core(ELIGIBLE, _place((1, 0, 0), (1, 0, 1)))
    assert _codes(v) == ["duplicate"]


# ── malformed placements ───────────────────────────────────────────────────────


def test_missing_asset_id_is_bad_placement():
    v = albums.validate_core(ELIGIBLE, [{"spread": 0, "slot": 0}])
    assert _codes(v) == ["bad_placement"]


def test_boolean_asset_id_is_rejected():
    # True is an int subclass; it must not be accepted as asset id 1.
    v = albums.validate_core(ELIGIBLE, [{"asset_id": True, "spread": 0, "slot": 0}])
    assert _codes(v) == ["bad_placement"]


def test_negative_spread_is_bad_placement():
    v = albums.validate_core(ELIGIBLE, [{"asset_id": 1, "spread": -1, "slot": 0}])
    assert _codes(v) == ["bad_placement"]


def test_non_integer_slot_is_bad_placement():
    v = albums.validate_core(ELIGIBLE, [{"asset_id": 1, "spread": 0, "slot": "front"}])
    assert _codes(v) == ["bad_placement"]


# ── exhaustiveness: report ALL issues, not just the first ───────────────────────


def test_validator_reports_every_issue_at_once():
    v = albums.validate_core(
        ELIGIBLE,
        _place((1, 0, 0), (2, 0, 0), (1, 1, 0), (99, 2, 0)),
    )
    # collision (slot 0,0), duplicate (asset 1), foreign (99) all surfaced together.
    assert set(_codes(v)) == {"slot_collision", "duplicate", "foreign_asset"}


def test_issue_order_is_stable():
    v = albums.validate_core(ELIGIBLE, _place((99, 0, 0), (1, 0, 0), (1, 1, 0)))
    # sorted by (code, asset, spread, slot) -> deterministic for a reviewer.
    assert _codes(v) == sorted(_codes(v))


# ── mock adapter + registry seam ────────────────────────────────────────────────


def test_mock_album_adapter_is_deterministic():
    a = mocks.MockAlbumAdapter()
    r = a.propose_album(7, asset_ids=[3, 1, 2])
    assert r.ok and r.capability is Capability.ALBUMS
    assert r.review is ReviewRequirement.HUMAN_REVIEW and r.model == "mock-albums-1"
    # sorted, one-per-spread, and the proposal is metadata-shaped only.
    assert r.output["spread_count"] == 3
    assert [p["asset_id"] for p in r.output["placements"]] == [1, 2, 3]
    assert a.propose_album(7, asset_ids=[3, 1, 2]).output == r.output


def test_mock_album_adapter_disabled():
    assert mocks.MockAlbumAdapter(enabled=False).propose_album(1).status is ResultStatus.DISABLED


def test_failing_adapter_album_path_is_non_ok():
    r = mocks.FailingAdapter(Capability.ALBUMS).propose_album(1, asset_ids=[1])
    assert r.status is ResultStatus.PROVIDER_ERROR and r.ok is False


def test_albums_has_no_default_provider():
    # ALBUMS is intentionally absent from the registry defaults: no production path yet,
    # so resolve() must raise rather than invent one.
    registry.reset()
    with pytest.raises(ValueError):
        registry.resolve(Capability.ALBUMS)


def test_registry_use_can_inject_album_adapter():
    registry.reset()
    mock = mocks.MockAlbumAdapter()
    with registry.use(Capability.ALBUMS, mock):
        assert registry.resolve(Capability.ALBUMS) is mock
    with pytest.raises(ValueError):
        registry.resolve(Capability.ALBUMS)
    registry.reset()
