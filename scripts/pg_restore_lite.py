"""Nap lai ban dump do pg_dump_lite.py tao ra.

Xoa sach du lieu schema `news` roi COPY lai. Cau truc bang phai co san - chay
`news-bot init-db` truoc.
"""
from __future__ import annotations

import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg

from news_bot.config import get_settings

SRC = os.environ.get("NEWS_RESTORE_SRC")
if not SRC or not os.path.exists(SRC):
    sys.exit(f"khong thay file dump: {SRC}")

dsn = get_settings().postgres_dsn.replace("postgresql+psycopg://", "postgresql://")

blocks: list[tuple[str, bytes]] = []
current: str | None = None
payload: list[bytes] = []

with gzip.open(SRC, "rb") as gz:
    for raw in gz:
        if raw.startswith(b"COPY news."):
            current = raw.decode().split()[1]        # news.<bang>
            payload = []
            continue
        if raw.strip() == b"\\." and current:
            blocks.append((current, b"".join(payload)))
            current = None
            continue
        if current:
            payload.append(raw)

with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        # TRUNCATE CASCADE mot lan cho tat ca, tranh vuong khoa ngoai.
        names = ", ".join(t for t, _ in blocks)
        if names:
            cur.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    for table, data in blocks:
        if not data:
            continue
        with conn.cursor() as cur, cur.copy(f"COPY {table} FROM STDIN") as copy:
            copy.write(data)
        print(f"  {table}: {len(data.splitlines())} dong")
    conn.commit()

print(f"da nap lai tu {SRC}")
