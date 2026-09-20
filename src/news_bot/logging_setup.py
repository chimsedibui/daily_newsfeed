"""structlog -> stdout (JSON cho Airflow/Loki) + sink Postgres theo run_id."""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_DB_SINK: Any = None


def set_db_sink(sink: Any) -> None:
    """sink(level, logger, event, context) -> ghi vào bảng news.app_log."""
    global _DB_SINK
    _DB_SINK = sink


def _postgres_processor(logger, method_name, event_dict):
    if _DB_SINK is not None and event_dict.get("run_id"):
        try:
            ctx = {k: v for k, v in event_dict.items() if k not in ("event", "level", "logger")}
            _DB_SINK(
                level=event_dict.get("level", method_name).upper(),
                logger=event_dict.get("logger", ""),
                event=str(event_dict.get("event", "")),
                context=ctx,
            )
        except Exception:  # logging không bao giờ được làm sập pipeline
            pass
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    renderer = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _postgres_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "news_bot"):
    return structlog.get_logger(name)
