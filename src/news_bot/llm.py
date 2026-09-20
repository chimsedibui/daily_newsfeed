"""Lop LLM: factory ChatAnthropic + bang gia de quy doi cost_usd cho bang llm_call."""
from __future__ import annotations

from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from .config import get_settings

# USD / 1 trieu token. Nguon: bang gia Anthropic API (first-party rates).
# Cache read ~0.1x input, cache write ~1.25x input.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-fable-5-1": {"input": 10.00, "output": 50.00},
}
_CACHE_READ_MULT = 0.1


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """Quy doi token -> USD. Model la khong biet gia -> tra 0 thay vi doan bua."""
    price = PRICING.get(model)
    if not price:
        return 0.0
    return (
        input_tokens * price["input"]
        + cache_read_tokens * price["input"] * _CACHE_READ_MULT
        + output_tokens * price["output"]
    ) / 1_000_000


def extract_usage(response) -> dict[str, int]:
    """Doc usage tu AIMessage cua langchain-anthropic (usage_metadata chuan LC)."""
    meta = getattr(response, "usage_metadata", None) or {}
    details = meta.get("input_token_details", {}) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
        "cache_read_tokens": int(details.get("cache_read", 0)),
    }


@lru_cache(maxsize=8)
def get_chat_model(model: str | None = None, max_tokens: int = 4096) -> ChatAnthropic:
    """ChatAnthropic dung chung. lru_cache de tai su dung HTTP connection pool."""
    s = get_settings()
    return ChatAnthropic(
        model=model or s.summarizer_model,
        api_key=s.anthropic_api_key or None,
        max_tokens=max_tokens,
        timeout=120,
        max_retries=3,
    )
