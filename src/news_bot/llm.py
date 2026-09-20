"""Lop LLM: factory model theo provider + bang gia de quy doi cost_usd.

Provider cam duoc qua LLM_PROVIDER (openai | vertex). Ly do khong thay thang:
giu duoc duong lui khi mot ben het credit hoac loi, va do duoc A/B bang chinh
bang llm_call thay vi tranh luan.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from .config import get_settings

# USD / 1 trieu token. Model id la duy nhat giua hai provider nen dung mot bang phang.
#
# OpenAI: platform.openai.com/docs/pricing, bac short context, tier Standard.
# Vertex: cloud.google.com/vertex-ai/generative-ai/pricing, vung `global`,
#         bac <= 200K token vao.
# Ca hai doc ngay 20/09/2026.
PRICING: dict[str, dict[str, float]] = {
    # ---- OpenAI ----
    "gpt-6-astra":   {"input": 10.00, "cached_input": 1.00, "output": 50.00},
    "gpt-5.6-sol":   {"input": 4.00,  "cached_input": 0.40, "output": 20.00},
    "gpt-5.6-terra": {"input": 2.00,  "cached_input": 0.20, "output": 12.00},
    "gpt-5.6-luna":  {"input": 0.20,  "cached_input": 0.02, "output": 1.20},
    # The he cu (08/2025). Gia moi token thap nhat NHUNG la model suy luan tieu
    # 3.000-6.000 token dau ra cho mot bai tom tat (luna chi ~220), nen thuc te
    # dat hon luna ~4 lan va cham hon ~8 lan. Do ngay 20/09/2026, xem docs/REPORT.md.
    "gpt-5-nano":    {"input": 0.05,  "cached_input": 0.005, "output": 0.40},
    "gpt-5-mini":    {"input": 0.25,  "cached_input": 0.025, "output": 2.00},
    "gpt-5.4-nano":  {"input": 0.20,  "cached_input": 0.02,  "output": 1.25},
    "gpt-5.4-mini":  {"input": 0.75,  "cached_input": 0.075, "output": 4.50},

    # ---- Vertex AI (Gemini) ----
    # Gia khuyen mai cua ho 3.x Flash het han 31/12/2026, sau do gap doi
    # ($1.50 / $7.50). Nho sua bang nay truoc moc do.
    "gemini-3.8-flash":      {"input": 0.75, "cached_input": 0.075, "output": 3.75},
    "gemini-3.7-flash":      {"input": 0.75, "cached_input": 0.075, "output": 3.75},
    "gemini-3.6-flash":      {"input": 0.75, "cached_input": 0.075, "output": 3.75},
    "gemini-3.5-flash":      {"input": 1.50, "cached_input": 0.15,  "output": 9.00},
    "gemini-3.5-flash-lite": {"input": 0.30, "cached_input": 0.03,  "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "cached_input": 0.01,  "output": 0.40},
    "gemini-3.1-pro-preview": {"input": 2.00, "cached_input": 0.20, "output": 12.00},
}


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> float:
    """Quy doi token -> USD. Model khong co trong bang -> tra 0 thay vi doan bua.

    Token suy luan cua Gemini nam NGOAI `output_tokens` cua LangChain (do thay:
    276 token ra nhung 588 token nghi), trong khi Google tinh tien chung theo
    gia output. Bo quen chung se bao cao thieu chi phi hon 3 lan o ho Flash.
    """
    price = PRICING.get(model)
    if not price:
        return 0.0
    # cache_read_tokens da nam trong input_tokens cua LangChain -> tru ra roi
    # tinh rieng, neu khong se tinh trung.
    billed_input = max(input_tokens - cache_read_tokens, 0)
    return (
        billed_input * price["input"]
        + cache_read_tokens * price["cached_input"]
        + (output_tokens + reasoning_tokens) * price["output"]
    ) / 1_000_000


def extract_usage(response: Any) -> dict[str, int]:
    """Doc usage tu AIMessage.

    `usage_metadata` la dang chuan hoa cua LangChain nen cac truong co ten giong
    nhau du chay provider nao.

    Canh bao: `reasoning_tokens` (Gemini: thoughtsTokenCount) KHONG nam trong
    `output_tokens`. Do thuc te tren gemini-3.8-flash: output 276, reasoning 588.
    Google van tinh tien chung theo gia output, nen estimate_cost_usd() phai
    cong ca hai - xem ham do.
    """
    meta = getattr(response, "usage_metadata", None) or {}
    in_details = meta.get("input_token_details", {}) or {}
    out_details = meta.get("output_token_details", {}) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
        "cache_read_tokens": int(in_details.get("cache_read", 0)),
        "reasoning_tokens": int(out_details.get("reasoning", 0)),
    }


def _build_openai(model: str, max_tokens: int):
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(
        model=model,
        api_key=s.openai_api_key or None,
        max_tokens=max_tokens,
        timeout=180,
        max_retries=3,
    )


def _build_vertex(model: str, max_tokens: int):
    # ChatVertexAI cua langchain-google-vertexai da deprecated tu LangChain 3.2;
    # ChatGoogleGenerativeAI voi vertexai=True la duong thay the, van di qua
    # Vertex AI va van dung ADC.
    from langchain_google_genai import ChatGoogleGenerativeAI

    s = get_settings()
    kwargs: dict[str, Any] = {
        "model": model,
        "vertexai": True,
        "project": s.vertex_project or None,
        "location": s.vertex_location,
        "max_output_tokens": max_tokens,
        "max_retries": 3,
    }
    # Ho Flash suy luan mac dinh va tieu vai tram token cho mot bai tom tat 200
    # token. thinking_budget=0 tat han; -1 de model tu quyet. Ban -lite khong
    # suy luan nen tham so nay vo hai voi chung.
    if s.vertex_thinking_budget >= 0:
        kwargs["thinking_budget"] = s.vertex_thinking_budget
    return ChatGoogleGenerativeAI(**kwargs)


BUILDERS = {"openai": _build_openai, "vertex": _build_vertex}


@lru_cache(maxsize=12)
def get_chat_model(model: str | None = None, max_tokens: int = 4096):
    """Model chat dung chung. lru_cache de tai su dung HTTP connection pool."""
    s = get_settings()
    build = BUILDERS.get(s.llm_provider)
    if build is None:
        raise ValueError(
            f"LLM_PROVIDER khong ho tro: {s.llm_provider!r} (chon: {sorted(BUILDERS)})"
        )
    return build(model or s.summarizer_model, max_tokens)
