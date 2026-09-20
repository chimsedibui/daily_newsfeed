"""Lop LLM: factory ChatOpenAI + bang gia de quy doi cost_usd cho bang llm_call."""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from .config import get_settings

# USD / 1 trieu token, bac "short context", tier Standard.
# Nguon: platform.openai.com/docs/pricing, doc ngay 20/09/2026.
#
# Hai luu y khi doi so:
#   - Bac "long context" dat gap doi. Prompt cua ta ~3k token/bai nen luon nam
#     o bac short; neu noi rong prompt thi phai kiem tra lai.
#   - Gia "cached input" chi ap dung khi bat prompt caching, hien chua bat.
PRICING: dict[str, dict[str, float]] = {
    "gpt-6-astra":   {"input": 10.00, "cached_input": 1.00, "output": 50.00},
    "gpt-5.6-sol":   {"input": 4.00,  "cached_input": 0.40, "output": 20.00},
    "gpt-5.6-terra": {"input": 2.00,  "cached_input": 0.20, "output": 12.00},
    "gpt-5.6-luna":  {"input": 0.20,  "cached_input": 0.02, "output": 1.20},
}


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """Quy doi token -> USD. Model khong co trong bang -> tra 0 thay vi doan bua."""
    price = PRICING.get(model)
    if not price:
        return 0.0
    # cache_read_tokens da nam trong input_tokens cua LangChain -> tru ra roi
    # tinh rieng, neu khong se tinh trung.
    billed_input = max(input_tokens - cache_read_tokens, 0)
    return (
        billed_input * price["input"]
        + cache_read_tokens * price["cached_input"]
        + output_tokens * price["output"]
    ) / 1_000_000


def extract_usage(response) -> dict[str, int]:
    """Doc usage tu AIMessage.

    `usage_metadata` la dang chuan hoa cua LangChain nen cac truong nay giong
    nhau du chay provider nao.
    """
    meta = getattr(response, "usage_metadata", None) or {}
    details = meta.get("input_token_details", {}) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
        "cache_read_tokens": int(details.get("cache_read", 0)),
    }


@lru_cache(maxsize=8)
def get_chat_model(model: str | None = None, max_tokens: int = 4096) -> ChatOpenAI:
    """ChatOpenAI dung chung. lru_cache de tai su dung HTTP connection pool.

    Ho gpt-5.x/gpt-6 la model suy luan: chung nhan `max_completion_tokens` chu
    khong phai `max_tokens`, va khong cho chinh `temperature`. langchain-openai
    tu anh xa `max_tokens` sang dung truong, nen o day khong dat temperature.
    """
    s = get_settings()
    return ChatOpenAI(
        model=model or s.summarizer_model,
        api_key=s.openai_api_key or None,
        max_tokens=max_tokens,
        timeout=180,
        max_retries=3,
    )
