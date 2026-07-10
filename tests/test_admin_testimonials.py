import asyncio

import pytest
from fastapi import HTTPException

from app.admin import testimonials

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("quote", "attribution_name"),
    [
        ("", "Avery"),
        ("   ", "Avery"),
        ("A thoughtful quote", ""),
        ("A thoughtful quote", "   "),
    ],
)
def test_required_testimonial_fields_reject_blank_values(quote, attribution_name):
    with pytest.raises(HTTPException) as exc:
        testimonials._validated_attribution(quote, attribution_name)

    assert exc.value.status_code == 400
    assert exc.value.detail == "quote and name required"


def test_required_testimonial_fields_are_normalized():
    assert testimonials._validated_attribution("  Excellent work.  ", "  Avery Lane  ") == (
        "Excellent work.",
        "Avery Lane",
    )


def test_update_validates_before_writing(monkeypatch):
    monkeypatch.setattr(testimonials.db, "get_or_404", lambda *args, **kwargs: {"id": 42})
    writes = []
    monkeypatch.setattr(testimonials.db, "run", lambda *args, **kwargs: writes.append(args))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            testimonials.update_testimonial(
                42,
                quote="   ",
                attribution_name="Avery Lane",
                business="",
                gallery_id=None,
                position=0,
                published=False,
            )
        )

    assert exc.value.status_code == 400
    assert writes == []
