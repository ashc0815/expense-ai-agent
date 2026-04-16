"""FX conversion service — reads mock rates from config/fx_rates.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "fx_rates.yaml"

with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _data = yaml.safe_load(_f)

_RATES: dict[str, float] = _data["rates"]

SUPPORTED_CURRENCIES = ["CNY", "AUD", "USD", "EUR", "JPY", "GBP"]


def get_rate(from_currency: str, to_currency: str) -> float:
    if from_currency == to_currency:
        return 1.0
    return round(_RATES[from_currency] / _RATES[to_currency], 6)


def convert(amount: float, from_currency: str, to_currency: str) -> float:
    if from_currency == to_currency:
        return amount
    amount_cny = amount * _RATES[from_currency]
    result = amount_cny / _RATES[to_currency]
    return round(result, 2)


def get_supported_currencies() -> list[str]:
    return list(SUPPORTED_CURRENCIES)


def get_all_rates() -> dict:
    return {"base": "CNY", "rates": dict(_RATES), "supported": SUPPORTED_CURRENCIES}
