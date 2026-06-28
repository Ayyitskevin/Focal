"""The published worker-output schemas are well-formed, and the vision schema stays in lockstep
with the deterministic validator that enforces it (app/qwen_writeback.parse_structured).

Unit (no DB/network): pins the schemas/ contract artifacts so they can't silently rot away
from the code, and proves the vision schema's rules are the rules the code actually applies.
"""

import json
from pathlib import Path

import pytest

from app import qwen_writeback

pytestmark = pytest.mark.unit

_SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"
_NAMES = ["vision", "products"]


def _load(name: str) -> dict:
    return json.loads((_SCHEMAS / f"{name}.schema.json").read_text())


def test_all_schemas_are_wellformed():
    for name in _NAMES:
        schema = _load(name)
        assert schema["$schema"].startswith("https://json-schema.org/")
        assert schema["$id"].endswith(f"{name}.schema.json")
        assert schema["title"] and schema["type"] == "object"


def test_vision_schema_matches_what_the_validator_accepts():
    # A document shaped per vision.schema.json is accepted + normalized by the code path that
    # consumes it, so the published contract and the enforcement can't drift apart.
    doc = {
        "photos": [
            {
                "basename": "p1.jpg",
                "keywords": ["plate", "table"],
                "alt_text": "a plated dish",
                "keeper_score": 0.9,
                "hero_potential": 0.7,
            }
        ]
    }
    out = qwen_writeback.parse_structured(doc)
    assert out == [
        {
            "basename": "p1.jpg",
            "keywords": ["plate", "table"],
            "alt_text": "a plated dish",
            "keeper_score": 0.9,
            "hero_potential": 0.7,
        }
    ]


def test_vision_schema_bounds_are_enforced_by_code():
    schema_photo = _load("vision")["properties"]["photos"]["items"]["properties"]
    # the schema declares [0,1] bounds on the scores...
    assert schema_photo["keeper_score"]["maximum"] == 1.0
    assert schema_photo["hero_potential"]["minimum"] == 0.0
    # ...and the validator actually rejects an out-of-range value, not just the schema.
    with pytest.raises(qwen_writeback.QwenWritebackError):
        qwen_writeback.parse_structured({"photos": [{"basename": "p.jpg", "keeper_score": 1.5}]})
    # a required basename is required by both the schema and the code.
    assert "basename" in _load("vision")["properties"]["photos"]["items"]["required"]
    with pytest.raises(qwen_writeback.QwenWritebackError):
        qwen_writeback.parse_structured({"photos": [{"keeper_score": 0.5}]})
