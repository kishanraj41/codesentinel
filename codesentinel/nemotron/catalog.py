"""Nebius Token Factory model ids and public catalog prices (USD per 1M tokens)."""

from __future__ import annotations

LIGHTNING = "nvidia/Nemotron-3_5-Lightning"
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
SUPER = "nvidia/nemotron-3-super-120b-a12b"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# input, output per 1M tokens. Source: Token Factory public catalog, Oct 2026.
PRICES = {
    LIGHTNING: (0.06, 0.24),
    NANO: (0.06, 0.24),
    SUPER: (0.30, 0.90),
    ULTRA: (1.00, 3.00),
}

CATALOG_PRICE_TABLE = {
    model: {"input": pin, "output": pout} for model, (pin, pout) in PRICES.items()
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = PRICES.get(model)
    if not prices:
        return 0.0
    pin, pout = prices
    return round((input_tokens / 1_000_000) * pin + (output_tokens / 1_000_000) * pout, 6)
