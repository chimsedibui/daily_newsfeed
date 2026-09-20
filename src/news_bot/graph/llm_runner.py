"""Goi LLM co structured output + ghi mot ban ghi llm_call cho moi lan goi.

`include_raw=True` la mau chot: no tra ve ca AIMessage goc, nho do van doc duoc
usage_metadata de tinh token/chi phi - dieu ma `with_structured_output` tran
khong cho.
"""
from __future__ import annotations

import time
from typing import TypeVar

from pydantic import BaseModel

from ..llm import estimate_cost_usd, extract_usage, get_chat_model
from ..logging_setup import get_logger
from ..tracing.store import TraceStore, span
from ..utils import truncate

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


def structured_call(
    schema: type[T],
    system: str,
    user: str,
    *,
    model: str,
    purpose: str,
    store: TraceStore | None,
    max_tokens: int = 4096,
) -> T | None:
    """Tra ve instance cua `schema`, hoac None neu model khong tra ve duoc dung dang."""
    chat = get_chat_model(model, max_tokens=max_tokens).with_structured_output(
        schema, include_raw=True
    )
    messages = [("system", system), ("human", user)]
    started = time.perf_counter()
    error: str | None = None
    parsed: T | None = None
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
    raw_text = ""

    try:
        result = chat.invoke(messages)
        raw = result.get("raw")
        parsed = result.get("parsed")
        if result.get("parsing_error"):
            error = f"parsing_error: {result['parsing_error']!r}"
        if raw is not None:
            usage = extract_usage(raw)
            raw_text = str(parsed) if parsed is not None else str(raw.content)
    except Exception as exc:
        error = repr(exc)
        log.warning("llm.failed", purpose=purpose, model=model, error=error)
    finally:
        latency = int((time.perf_counter() - started) * 1000)
        if store is not None:
            store.record_llm(
                model=model,
                purpose=purpose,
                usage=usage,
                latency_ms=latency,
                cost_usd=estimate_cost_usd(model, **usage),
                prompt_preview=truncate(user, 1000),
                output_preview=truncate(raw_text, 1000),
                error=error,
            )
    return parsed


__all__ = ["span", "structured_call"]
