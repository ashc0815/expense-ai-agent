"""FX service unit tests."""
from __future__ import annotations

import pytest


def test_same_currency_returns_unchanged():
    from backend.services.fx_service import convert
    assert convert(100.0, "CNY", "CNY") == 100.0


def test_convert_usd_to_cny():
    from backend.services.fx_service import convert
    assert convert(100.0, "USD", "CNY") == 725.0


def test_convert_usd_to_aud():
    from backend.services.fx_service import convert
    result = convert(100.0, "USD", "AUD")
    assert result == 154.26


def test_convert_jpy_to_cny():
    from backend.services.fx_service import convert
    assert convert(10000.0, "JPY", "CNY") == 480.0


def test_get_rate_same_currency():
    from backend.services.fx_service import get_rate
    assert get_rate("CNY", "CNY") == 1.0


def test_get_rate_usd_to_aud():
    from backend.services.fx_service import get_rate
    rate = get_rate("USD", "AUD")
    assert rate == round(7.25 / 4.70, 6)


def test_get_rate_jpy_to_gbp():
    from backend.services.fx_service import get_rate
    rate = get_rate("JPY", "GBP")
    assert rate == round(0.048 / 9.20, 6)


def test_get_supported_currencies():
    from backend.services.fx_service import get_supported_currencies
    result = get_supported_currencies()
    assert result == ["CNY", "AUD", "USD", "EUR", "JPY", "GBP"]


def test_convert_zero_amount():
    from backend.services.fx_service import convert
    assert convert(0.0, "USD", "CNY") == 0.0


def test_unsupported_currency_raises():
    from backend.services.fx_service import convert
    with pytest.raises(KeyError):
        convert(100.0, "BRL", "CNY")
