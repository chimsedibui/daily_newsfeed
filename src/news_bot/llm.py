"""Lop LLM: chuoi provider du phong + bang gia de quy doi cost_usd.

Thu tu provider lay tu LLM_PROVIDERS (mac dinh: gemini, vertex, openai). Moi
provider co bang model rieng - `gemini-3.1-flash-lite` khong ton tai tren OpenAI
va nguoc lai - nen chuyen provider cung la chuyen model.

Provider hong kieu he thong (sai key, het quota, khong ket noi duoc) bi danh dau
"down" va bo qua o cac lan goi sau TRONG CUNG TIEN TRINH. Neu khong, mot su co
cua Gemini se lam ca 36 bai deu thu Gemini truoc roi moi sang Vertex - gap doi
do tre ma khong duoc gi.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from .config import get_settings
from .logging_setup import get_logger

log = get_logger(__name__)

# USD / 1 trieu token.
#   OpenAI: platform.openai.com/docs/pricing (short context, tier Standard)
#   Gemini: ai.google.dev/gemini-api/docs/pricing (paid tier)
#   Vertex: cloud.google.com/vertex-ai/generative-ai/pricing (vung global, <=200K)
# Ca ba doc ngay 20/09/2026.
PRICING: dict[str, dict[str, float]] = {
    "gpt-6-astra":   {"input": 10.00, "cached_input": 1.00, "output": 50.00},
    "gpt-5.6-sol":   {"input": 4.00,  "cached_input": 0.40, "output": 20.00},
    "gpt-5.6-terra": {"input": 2.00,  "cached_input": 0.20, "output": 12.00},
    "gpt-5.6-luna":  {"input": 0.20,  "cached_input": 0.02, "output": 1.20},
    # The he cu (08/2025). Gia moi token thap nhat NHUNG la model suy luan tieu
    # 3.000-6.000 token dau ra cho mot ban tom tat 220 token, nen thuc te dat
    # hon luna ~4 lan va cham hon ~8 lan. Xem docs/REPORT.md muc 4b.
    "gpt-5-nano":    {"input": 0.05,  "cached_input": 0.005, "output": 0.40},
    "gpt-5-mini":    {"input": 0.25,  "cached_input": 0.025, "output": 2.00},

    # Gia khuyen mai cua ho 3.x Flash het han 31/12/2026, sau do gap doi.
    "gemini-3.8-flash":      {"input": 0.75, "cached_input": 0.075, "output": 3.75},
    "gemini-3.7-flash":      {"input": 0.75, "cached_input": 0.075, "output": 3.75},
    "gemini-3.6-flash":      {"input": 0.75, "cached_input": 0.075, "output": 3.75},
    "gemini-3.5-flash":      {"input": 1.50, "cached_input": 0.15,  "output": 9.00},
    "gemini-3.5-flash-lite": {"input": 0.30, "cached_input": 0.03,  "output": 2.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "cached_input": 0.025, "output": 1.50},
    # Chi con tren Vertex: Developer API khong mo cho nguoi dung moi nua.
    "gemini-2.5-flash-lite": {"input": 0.10, "cached_input": 0.01,  "output": 0.40},
}

# Model cua tung provider cho tung viec. Doi provider la doi model.
PROVIDER_MODELS: dict[str, dict[str, str]] = {
    "gemini": {"summarize": "gemini-3.1-flash-lite", "compose": "gemini-3.8-flash"},
    "vertex": {"summarize": "gemini-2.5-flash-lite", "compose": "gemini-3.5-flash-lite"},
    "openai": {"summarize": "gpt-5.6-luna", "compose": "gpt-5.6-luna"},
}

# Provider da hong kieu he thong trong tien trinh nay -> bo qua o cac lan sau.
_DOWN: set[str] = set()

# Dau hieu provider hong ca cum, khong phai hong mot bai.
_SYSTEMIC = (
    "authentication", "unauthenticated", "permission", "api key", "api_key",
    "quota", "resource_exhausted", "429", "401", "403",
    "connection", "timeout", "unavailable", "503",
)


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

    Canh bao: `reasoning_tokens` (Gemini: thoughtsTokenCount) KHONG nam trong
    `output_tokens`. Do thuc te tren gemini-3.8-flash: output 276, reasoning 588.
    Google van tinh tien chung theo gia output - xem estimate_cost_usd().
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


def provider_chain() -> list[str]:
    """Thu tu provider, da bo nhung cai thieu cau hinh hoac dang down."""
    s = get_settings()
    needs = {"gemini": s.gemini_api_key, "openai": s.openai_api_key,
             "vertex": s.vertex_project}
    chain: list[str] = []
    for name in (p.strip() for p in s.llm_providers.split(",")):
        if not name or name in chain or name in _DOWN:
            continue
        if name not in BUILDERS:
            log.warning("llm.unknown_provider", provider=name)
            continue
        if not needs.get(name):
            continue
        chain.append(name)
    return chain


def model_for(provider: str, purpose: str) -> str:
    """Model cua provider nay cho viec nay.

    SUMMARIZER_MODEL / EDITOR_MODEL trong .env chi ap dung cho provider DAU
    chuoi - provider du phong phai dung model cua chinh no, vi mot ten model
    cua Gemini khong co nghia gi voi OpenAI.
    """
    s = get_settings()
    chain = provider_chain()
    if chain and provider == chain[0]:
        override = s.summarizer_model if purpose == "summarize" else s.editor_model
        if override:
            return override
    table = PROVIDER_MODELS.get(provider, {})
    return table.get(purpose) or table.get("summarize", "")


def mark_down(provider: str, error: str) -> bool:
    """Danh dau provider hong kieu he thong. Tra ve True neu vua danh dau."""
    lowered = error.lower()
    if not any(token in lowered for token in _SYSTEMIC):
        return False
    if provider in _DOWN:
        return False
    _DOWN.add(provider)
    log.warning("llm.provider_down", provider=provider, error=error[:200])
    return True


def down_providers() -> set[str]:
    return set(_DOWN)


def reset_down() -> None:
    """Xoa danh sach down. Goi o dau moi run va trong test."""
    _DOWN.clear()


def _wants_thinking_budget(model: str) -> bool:
    """Chi gui thinking_budget cho model thuc su biet suy luan.

    Ban -lite khong suy luan (reasoning_tokens luon 0), va it nhat
    gemini-3.5-flash-lite tra 400 INVALID_ARGUMENT khi nhan tham so nay.
    """
    return "lite" not in model.lower()


def _build_openai(model: str, max_tokens: int, thinking_budget: int):
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(
        model=model,
        api_key=s.openai_api_key or None,
        max_tokens=max_tokens,
        timeout=180,
        max_retries=2,
    )


def _google_kwargs(model: str, max_tokens: int, thinking_budget: int) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_output_tokens": max_tokens,
        "max_retries": 2,
    }
    if thinking_budget >= 0 and _wants_thinking_budget(model):
        kwargs["thinking_budget"] = thinking_budget
    return kwargs


def _build_vertex(model: str, max_tokens: int, thinking_budget: int):
    # ChatVertexAI cua langchain-google-vertexai da deprecated tu LangChain 3.2;
    # ChatGoogleGenerativeAI voi vertexai=True la duong thay the, van dung ADC.
    from langchain_google_genai import ChatGoogleGenerativeAI

    s = get_settings()
    return ChatGoogleGenerativeAI(
        vertexai=True,
        project=s.vertex_project or None,
        location=s.vertex_location,
        **_google_kwargs(model, max_tokens, thinking_budget),
    )


def _build_gemini(model: str, max_tokens: int, thinking_budget: int):
    """Gemini Developer API: xac thuc bang API key, KHONG qua Vertex/ADC.

    Khac biet dang ke voi `vertex`: tien di vao tai khoan gan voi API key chu
    khong vao project GCP, va key khong het han nhu ADC cua tai khoan nguoi dung.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    s = get_settings()
    return ChatGoogleGenerativeAI(
        google_api_key=s.gemini_api_key or None,
        **_google_kwargs(model, max_tokens, thinking_budget),
    )


BUILDERS = {
    "openai": _build_openai,
    "vertex": _build_vertex,
    "gemini": _build_gemini,
}


@lru_cache(maxsize=24)
def get_chat_model(provider: str, model: str, max_tokens: int, thinking_budget: int):
    """Model chat dung chung. lru_cache de tai su dung HTTP connection pool."""
    build = BUILDERS.get(provider)
    if build is None:
        raise ValueError(f"provider khong ho tro: {provider!r} (chon: {sorted(BUILDERS)})")
    return build(model, max_tokens, thinking_budget)
