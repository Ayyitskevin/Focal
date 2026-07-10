"""Lock the behavior of the shared admin helpers extracted from duplicated copies.

`initials` and `flash_redirect` replaced byte-identical private copies in
financials.py / inbox.py and validation.py / vision_cutover.py respectively.
These tests pin the exact behavior that must not drift during the consolidation —
in particular the two divergent empty-name fallbacks and the (deliberate)
absence of URL-encoding on the flash params.
"""

import pytest

from app.admin import common

pytestmark = pytest.mark.unit


# ── initials ─────────────────────────────────────────────────────────────────


def test_initials_two_names_takes_first_and_last():
    assert common.initials("Osteria Uno") == "OU"
    assert common.initials("Mary Jane Watson") == "MW"


def test_initials_single_name_takes_first_two_letters():
    assert common.initials("Cher") == "CH"


def test_initials_empty_fallback_defaults_to_question_mark():
    # financials.py's client cards used "?".
    assert common.initials("") == "?"
    assert common.initials(None) == "?"
    assert common.initials("   ") == "?"


def test_initials_empty_fallback_is_overridable():
    # inbox.py's lead rows used "#".
    assert common.initials("", empty="#") == "#"
    assert common.initials(None, empty="#") == "#"


# ── flash_redirect ───────────────────────────────────────────────────────────


def test_flash_redirect_no_params_is_a_bare_303():
    resp = common.flash_redirect("/admin/validation")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/validation"


def test_flash_redirect_msg_only():
    resp = common.flash_redirect("/admin/validation", "saved")
    assert resp.headers["location"] == "/admin/validation?msg=saved"


def test_flash_redirect_err_only():
    resp = common.flash_redirect("/admin/vision-cutover", err="nope")
    assert resp.headers["location"] == "/admin/vision-cutover?err=nope"


def test_flash_redirect_both_params_msg_first():
    resp = common.flash_redirect("/admin/validation", "saved", "warned")
    assert resp.headers["location"] == "/admin/validation?msg=saved&err=warned"


def test_flash_redirect_builds_the_query_by_raw_concatenation():
    # The helper concatenates params with an f-string and no manual encoding —
    # exactly as the two original _redirect copies did. Starlette's RedirectResponse
    # then percent-encodes the Location header, so the observable result is identical
    # before and after the consolidation (this is the no-op we're locking in).
    resp = common.flash_redirect("/admin/validation", "a b")
    assert resp.headers["location"] == "/admin/validation?msg=a%20b"
