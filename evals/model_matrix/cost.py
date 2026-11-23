"""Eval-only prices.

Vendor parameter models reject unknown fields, so these rates stay out of
``configs/models.yaml``. The arithmetic is the production ``quote_cost``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from enterprise_agent.infrastructure.observability.metrics import quote_cost

_ROOT = Path(__file__).parent
_INPUT = "input_usd_per_million"
_OUTPUT = "output_usd_per_million"


def load_prices(path: Path | None = None) -> dict[str, Any]:
    source = path or _ROOT / "prices.yaml"
    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("price file must be a mapping")
    return loaded


def deepseek_tier(moment: datetime, spec: dict[str, Any]) -> str:
    """Return peak or off_peak from the weekday UTC windows in the price file."""
    current = moment.astimezone(UTC)
    windows = spec.get("peak_utc", {})
    weekdays = set(windows.get("weekdays", []))
    if current.weekday() not in weekdays:
        return "off_peak"
    hour = current.hour + current.minute / 60 + current.second / 3600
    for start, end in windows.get("windows", []):
        if float(start) <= hour < float(end):
            return "peak"
    return "off_peak"


def rate_for(profile: str, prices: dict[str, Any], moment: datetime) -> dict[str, Any]:
    profiles = prices["profiles"]
    if profile not in profiles:
        raise KeyError(f"no eval price for profile {profile}")
    spec = profiles[profile]
    if "tiers" in spec:
        tier = deepseek_tier(moment, spec)
        rates = spec["tiers"][tier]
    else:
        tier = "standard"
        rates = spec
    return {
        "tier": tier,
        "currency": spec["currency"],
        "input_per_million": float(rates["input_per_million"]),
        "output_per_million": float(rates["output_per_million"]),
        "source": spec.get("source"),
        "note": spec.get("note"),
    }


def cost_for(
    profile: str,
    prices: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
    moment: datetime,
) -> dict[str, Any]:
    """Price one run and convert CNY rows to USD with the configured parity."""
    rate = rate_for(profile, prices, moment)
    native = quote_cost(
        {
            _INPUT: rate["input_per_million"],
            _OUTPUT: rate["output_per_million"],
        },
        input_tokens,
        output_tokens,
        offline=False,
    )
    if native is None:
        raise RuntimeError("quote_cost returned no price for a complete eval rate")
    currency = str(rate["currency"])
    fx = float(prices["fx"]["cny_per_usd"])
    usd = native / fx if currency == "CNY" else native
    return {
        "currency": currency,
        "tier": rate["tier"],
        "input_per_million": rate["input_per_million"],
        "output_per_million": rate["output_per_million"],
        "cost_native": native,
        "cost_usd": usd,
        "fx_cny_per_usd": fx,
        "source": rate["source"],
    }
