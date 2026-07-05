"""Pin the Stripe API version to the tested contract, independent of the SDK.

Every stripe-python bump ships a new *default* pinned API version (15.2.1 →
2026-05-27.dahlia, 15.3.0 → 2026-06-24.dahlia). Without an explicit pin, a routine
dependency update silently changes the request/response contract on the money
path. Mise's _stripe() helpers set stripe.api_version to config.STRIPE_API_VERSION
so an SDK bump is a pure library update and moving the contract is deliberate.
"""

import pytest

from app import config, saas
from app.public import pay

pytestmark = pytest.mark.unit


@pytest.fixture
def restore_api_version():
    import stripe

    original = stripe.api_version
    yield
    stripe.api_version = original


def test_config_default_pins_the_tested_contract():
    # The version 15.2.1 (the pinned SDK) is built against — locking current behavior.
    assert config.STRIPE_API_VERSION == "2026-05-27.dahlia"


@pytest.mark.parametrize("get_stripe", [saas._stripe, pay._stripe])
def test_stripe_helper_pins_api_version(get_stripe, restore_api_version, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_API_VERSION", "2026-05-27.dahlia")
    assert get_stripe().api_version == "2026-05-27.dahlia"


@pytest.mark.parametrize("get_stripe", [saas._stripe, pay._stripe])
def test_helper_overrides_an_sdk_changed_default(get_stripe, restore_api_version, monkeypatch):
    # Simulate an SDK bump that moved the module default (what #76's 15.3.0 does):
    import stripe

    stripe.api_version = "2026-06-24.dahlia"  # the "new SDK default"
    monkeypatch.setattr(config, "STRIPE_API_VERSION", "2026-05-27.dahlia")
    # _stripe() must pull it back to OUR pin, neutralizing the bump.
    assert get_stripe().api_version == "2026-05-27.dahlia"


@pytest.mark.parametrize("get_stripe", [saas._stripe, pay._stripe])
def test_empty_pin_defers_to_the_sdk_default(get_stripe, restore_api_version, monkeypatch):
    import stripe

    stripe.api_version = "sdk-default-version"
    monkeypatch.setattr(config, "STRIPE_API_VERSION", "")
    # Explicitly unpinned (operator opt-out): leave the SDK's own default alone.
    assert get_stripe().api_version == "sdk-default-version"
