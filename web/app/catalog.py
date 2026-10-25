"""Token Factory catalog prices used to cost a scripted receipt.

Prices are USD per 1M tokens (input, output), from the public catalog
cited in the project brief. A cached receipt is an estimate, not a bill.
"""

from __future__ import annotations

LIGHTNING = "nvidia/Nemotron-3_5-Lightning"
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
SUPER = "nvidia/nemotron-3-super-120b-a12b"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"

PRICES: dict[str, tuple[float, float]] = {
    LIGHTNING: (0.06, 0.24),
    NANO: (0.06, 0.24),
    SUPER: (0.30, 0.90),
    ULTRA: (1.00, 3.00),
}

SHORT = {
    LIGHTNING: "Lightning",
    NANO: "Nano",
    SUPER: "Super",
    ULTRA: "Ultra",
}


def cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float:
    if not model or model not in PRICES or (input_tokens == 0 and output_tokens == 0):
        return 0.0
    pin, pout = PRICES[model]
    return round(input_tokens / 1_000_000 * pin + output_tokens / 1_000_000 * pout, 6)


def price_table() -> dict[str, dict[str, float]]:
    return {model: {"input": pin, "output": pout} for model, (pin, pout) in PRICES.items()}
