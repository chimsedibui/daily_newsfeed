"""Trace store: run + span + llm_call ghi thang vao Postgres.

Thiet ke: khong phu thuoc LangSmith/Langfuse. Moi node LangGraph mo 1 span qua
context manager `span(...)`; span id giu trong contextvar nen log va llm_call
tu dong gan dung parent ma khong phai truyen tay xuong tung ham.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text

from ..db.engine import session_scope
from ..logging_setup import get_logger, set_db_sink
from ..utils import truncate

log = get_logger(__name__)

_CURRENT_RUN: ContextVar[str | None] = ContextVar("current_run_id", default=None)
_CURRENT_SPAN: ContextVar[str | None] = ContextVar("current_span_id", default=None)

_PREVIEW_LIMIT = 2000


def current_run_id() -> str | None:
    return _CURRENT_RUN.get()


def current_span_id() -> str | None:
    return _CURRENT_SPAN.get()


def _jsonable(value: Any) -> Any:
    """Thu gon payload truoc khi ghi DB - trace khong phai noi luu ban goc."""
    if value is None:
        return None
    try:
        dumped = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        dumped = json.dumps(str(value), ensure_ascii=False)
    if len(dumped) > _PREVIEW_LIMIT:
        return {"_truncated": True, "preview": dumped[:_PREVIEW_LIMIT]}
    return json.loads(dumped)


class TraceStore:
    """Vong doi 1 pipeline run."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or str(uuid.uuid4())
        self._t0 = time.perf_counter()

    # ---------- run ----------
    def start_run(
        self,
        logical_date: date,
        trigger: str = "manual",
        dag_run_id: str | None = None,
        config_snapshot: dict | None = None,
    ) -> str:
        with session_scope() as s:
            s.execute(
                text(
                    """
                    INSERT INTO pipeline_run (run_id, logical_date, trigger, dag_run_id,
                                              status, config_snapshot)
                    VALUES (:run_id, :logical_date, :trigger, :dag_run_id, 'running',
                            CAST(:cfg AS jsonb))
                    ON CONFLICT (run_id) DO NOTHING
                    """
                ),
                {
                    "run_id": self.run_id,
                    "logical_date": logical_date,
                    "trigger": trigger,
                    "dag_run_id": dag_run_id,
                    "cfg": json.dumps(config_snapshot or {}, ensure_ascii=False, default=str),
                },
            )
        _CURRENT_RUN.set(self.run_id)
        set_db_sink(self._write_log)
        log.info("run.started", run_id=self.run_id, logical_date=str(logical_date))
        return self.run_id

    def finish_run(self, status: str, metrics: dict | None = None,
                   error: str | None = None) -> None:
        # duration_ms phai tinh tu started_at trong DB, KHONG tu dong ho trong
        # tien trinh: Airflow goi finalize() o mot task/tien trinh khac voi task
        # da tao run, nen self._t0 o day chi vai mili giay tuoi.
        with session_scope() as s:
            duration = s.execute(
                text(
                    """
                    UPDATE pipeline_run
                    SET status = :status,
                        finished_at = now(),
                        duration_ms = GREATEST(
                            0, (EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int
                        ),
                        metrics = metrics || CAST(:metrics AS jsonb),
                        error = :error
                    WHERE run_id = :run_id
                    RETURNING duration_ms
                    """
                ),
                {
                    "run_id": self.run_id,
                    "status": status,
                    "metrics": json.dumps(metrics or {}, ensure_ascii=False, default=str),
                    "error": truncate(error or "", 4000) or None,
                },
            ).scalar()
        log.info("run.finished", run_id=self.run_id, status=status, duration_ms=duration)

    def attach(self) -> None:
        """Gan run hien tai vao contextvar (dung khi resume run tu Airflow XCom)."""
        _CURRENT_RUN.set(self.run_id)
        set_db_sink(self._write_log)

    # ---------- span ----------
    def open_span(self, name: str, kind: str = "node", attributes: dict | None = None,
                  input_preview: Any = None) -> str:
        span_id = str(uuid.uuid4())
        with session_scope() as s:
            s.execute(
                text(
                    """
                    INSERT INTO node_span (span_id, run_id, parent_span_id, name, kind,
                                           attributes, input_preview)
                    VALUES (:span_id, :run_id, :parent, :name, :kind,
                            CAST(:attrs AS jsonb), CAST(:inp AS jsonb))
                    """
                ),
                {
                    "span_id": span_id,
                    "run_id": self.run_id,
                    "parent": _CURRENT_SPAN.get(),
                    "name": name,
                    "kind": kind,
                    "attrs": json.dumps(attributes or {}, ensure_ascii=False, default=str),
                    "inp": json.dumps(_jsonable(input_preview), ensure_ascii=False),
                },
            )
        return span_id

    def close_span(self, span_id: str, status: str, started: float,
                   output_preview: Any = None, error: str | None = None) -> None:
        with session_scope() as s:
            s.execute(
                text(
                    """
                    UPDATE node_span
                    SET status = :status, finished_at = now(), duration_ms = :duration,
                        output_preview = CAST(:out AS jsonb), error = :error
                    WHERE span_id = :span_id
                    """
                ),
                {
                    "span_id": span_id,
                    "status": status,
                    "duration": int((time.perf_counter() - started) * 1000),
                    "out": json.dumps(_jsonable(output_preview), ensure_ascii=False),
                    "error": truncate(error or "", 4000) or None,
                },
            )

    # ---------- llm ----------
    def record_llm(self, model: str, purpose: str, usage: dict, latency_ms: int,
                   cost_usd: float, prompt_preview: str = "", output_preview: str = "",
                   error: str | None = None) -> None:
        with session_scope() as s:
            s.execute(
                text(
                    """
                    INSERT INTO llm_call (run_id, span_id, model, purpose, input_tokens,
                                          output_tokens, cache_read_tokens,
                                          reasoning_tokens, cost_usd,
                                          latency_ms, prompt_preview, output_preview, error)
                    VALUES (:run_id, :span_id, :model, :purpose, :inp, :out, :cache,
                            :reason, :cost, :latency, :pprev, :oprev, :error)
                    """
                ),
                {
                    "run_id": self.run_id,
                    "span_id": _CURRENT_SPAN.get(),
                    "model": model,
                    "purpose": purpose,
                    "inp": usage.get("input_tokens", 0),
                    "out": usage.get("output_tokens", 0),
                    "cache": usage.get("cache_read_tokens", 0),
                    "reason": usage.get("reasoning_tokens", 0),
                    "cost": cost_usd,
                    "latency": latency_ms,
                    "pprev": truncate(prompt_preview, 1000),
                    "oprev": truncate(output_preview, 1000),
                    "error": truncate(error or "", 2000) or None,
                },
            )

    # ---------- log sink ----------
    def _write_log(self, level: str, logger: str, event: str, context: dict) -> None:
        with session_scope() as s:
            s.execute(
                text(
                    """
                    INSERT INTO app_log (run_id, span_id, ts, level, logger, event, context)
                    VALUES (:run_id, :span_id, :ts, :level, :logger, :event,
                            CAST(:ctx AS jsonb))
                    """
                ),
                {
                    "run_id": self.run_id,
                    "span_id": _CURRENT_SPAN.get(),
                    "ts": datetime.now(UTC),
                    "level": level,
                    "logger": logger,
                    "event": event,
                    "ctx": json.dumps(_jsonable(context), ensure_ascii=False),
                },
            )


@contextmanager
def span(store: TraceStore | None, name: str, kind: str = "node",
         attributes: dict | None = None, input_preview: Any = None) -> Iterator[dict]:
    """Mo mot span. `yield` ra dict de node ghi ket qua vao `out["output"]`.

    store=None -> no-op, tien cho unit test khong can Postgres.
    """
    if store is None:
        yield {"span_id": None, "output": None}
        return

    span_id = store.open_span(name, kind, attributes, input_preview)
    token = _CURRENT_SPAN.set(span_id)
    started = time.perf_counter()
    holder: dict = {"span_id": span_id, "output": None}
    try:
        yield holder
    except Exception as exc:
        store.close_span(span_id, "failed", started, holder.get("output"), repr(exc))
        _CURRENT_SPAN.reset(token)
        raise
    else:
        store.close_span(span_id, "success", started, holder.get("output"))
        _CURRENT_SPAN.reset(token)
