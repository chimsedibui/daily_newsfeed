"""Dump schema `news` ra SQL nen gzip, chi dung psycopg.

Ly do ton tai: goi Postgres rootless (io.zonky.test) chi co server, khong kem
pg_dump. Cai postgresql-client can sudo. Script nay dung COPY ... TO STDOUT nen
nhanh va khong can binary nao ngoai .venv.

Gioi han: chi dump DU LIEU cua schema `news`. Cau truc bang do sql/001_init.sql
dung lai (idempotent), nen ban restore se chay init-db truoc.
"""
from __future__ import annotations

import gzip
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg

from news_bot.config import get_settings

OUT = os.environ.get("NEWS_BACKUP_OUT")
if not OUT:
    sys.exit("thieu NEWS_BACKUP_OUT")

dsn = get_settings().postgres_dsn.replace("postgresql+psycopg://", "postgresql://")

# Thu tu quan trong khi restore: bang cha truoc, bang con sau (khoa ngoai).
TABLES = [
    "article",
    "pipeline_run",
    "node_span",
    "llm_call",
    "app_log",
    "source_health",
    "article_summary",
    "digest",
]

rows_total = 0
with psycopg.connect(dsn) as conn, gzip.open(OUT, "wb") as gz:
    gz.write(b"-- news-bot logical dump\n")
    gz.write(b"-- Chay `news-bot init-db` truoc khi restore de co san cau truc bang.\n")
    gz.write(b"SET search_path TO news, public;\n")
    for table in TABLES:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM news.{table}")
            n = cur.fetchone()[0]
        rows_total += n
        gz.write(f"\n-- {table}: {n} dong\n".encode())
        gz.write(f"COPY news.{table} FROM stdin;\n".encode())
        buf = io.BytesIO()
        with conn.cursor() as cur, cur.copy(f"COPY news.{table} TO STDOUT") as copy:
            for chunk in copy:
                buf.write(bytes(chunk))
        gz.write(buf.getvalue())
        gz.write(b"\\.\n")

print(f"da dump {rows_total} dong tu {len(TABLES)} bang -> {OUT}", file=sys.stderr)
