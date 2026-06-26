"""Qwen writeback — the deterministic structured-reply validator (pure unit).

No DB/network: pins the "model proposes, deterministic code validates" floor (audit §11.4)
— a malformed or out-of-range model reply is rejected, never normalized into something the
writeback would persist.
"""

import pytest

from app import qwen_writeback as q

pytestmark = pytest.mark.unit


def test_parses_strict_json():
    out = q.parse_structured(
        '{"photos":[{"basename":"P0.JPG","keywords":["plate","steak"],'
        '"alt_text":"a plated steak","keeper_score":0.8,"hero_potential":0.6}]}'
    )
    assert out == [
        {
            "basename": "P0.JPG",
            "keywords": ["plate", "steak"],
            "alt_text": "a plated steak",
            "keeper_score": 0.8,
            "hero_potential": 0.6,
        }
    ]


def test_tolerates_code_fence_and_prose_wrapping():
    out = q.parse_structured('here you go:\n```json\n{"photos":[{"basename":"x.jpg"}]}\n```')
    assert len(out) == 1 and out[0]["basename"] == "x.jpg"
    # optional fields default cleanly
    assert out[0]["keywords"] == [] and out[0]["alt_text"] is None
    assert out[0]["keeper_score"] is None and out[0]["hero_potential"] is None


def test_accepts_bare_list():
    assert len(q.parse_structured([{"basename": "a.jpg"}])) == 1


def test_rejects_unparseable():
    with pytest.raises(q.QwenWritebackError):
        q.parse_structured("not json at all")


def test_rejects_missing_photos_key():
    with pytest.raises(q.QwenWritebackError):
        q.parse_structured('{"results": []}')


def test_rejects_missing_basename():
    with pytest.raises(q.QwenWritebackError):
        q.parse_structured('{"photos":[{"keywords":["x"]}]}')


def test_rejects_out_of_range_score():
    with pytest.raises(q.QwenWritebackError):
        q.parse_structured('{"photos":[{"basename":"p.jpg","keeper_score":1.5}]}')
    with pytest.raises(q.QwenWritebackError):
        q.parse_structured('{"photos":[{"basename":"p.jpg","hero_potential":-0.1}]}')


def test_rejects_non_numeric_score_and_bool():
    with pytest.raises(q.QwenWritebackError):
        q.parse_structured('{"photos":[{"basename":"p.jpg","keeper_score":"high"}]}')
    # bool must not sneak through as a number
    with pytest.raises(q.QwenWritebackError):
        q.parse_structured({"photos": [{"basename": "p.jpg", "keeper_score": True}]})


def test_rejects_bad_keywords_type():
    with pytest.raises(q.QwenWritebackError):
        q.parse_structured('{"photos":[{"basename":"p.jpg","keywords":"plate"}]}')
