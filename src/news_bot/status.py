"""Trang trang thai: xem run, ban tin va suc khoe nguon ma khong can mo Google Chat.

Dung http.server cua thu vien chuan - khong them FastAPI/Flask cho mot trang
chi doc. Chi nghe loopback.

    python -m news_bot.status          # cong lay tu config/ports.env
    NEWS_STATUS_PORT=9000 python -m news_bot.status
"""
from __future__ import annotations

import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sqlalchemy import text

from .config import REPO_ROOT, get_settings
from .db.engine import session_scope
from .llm import down_providers, provider_chain
from .logging_setup import configure_logging, get_logger
from .utils import today_in

log = get_logger(__name__)

_TAG = re.compile(r"<[^>]+>")


def _read_port() -> tuple[str, int]:
    """Cong va host lay tu config/ports.env, env de o ngoai de ghi de."""
    host, port = "127.0.0.1", 18081
    ports_file = Path(REPO_ROOT) / "config" / "ports.env"
    if ports_file.exists():
        for line in ports_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("NEWS_STATUS_PORT="):
                port = int(line.split("=", 1)[1])
            elif line.startswith("NEWS_BIND_HOST="):
                host = line.split("=", 1)[1]
    return os.environ.get("NEWS_BIND_HOST", host), int(
        os.environ.get("NEWS_STATUS_PORT", port)
    )


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with session_scope() as s:
        return [dict(r) for r in s.execute(text(sql), params or {}).mappings().all()]


# ---------------------------------------------------------------- truy van
RUNS = """
    SELECT run_id, logical_date, trigger, status, started_at, duration_ms,
           articles_new, articles_loaded, articles_selected,
           llm_cost_usd, failed_sources, stale_sources
    FROM news.v_run_overview
    ORDER BY started_at DESC LIMIT 15
"""

DIGESTS = """
    SELECT d.id, d.group_key, d.digest_date, d.status, d.headline, d.overview,
           d.sent_at, jsonb_array_length(d.article_ids) AS n_bai, d.payload
    FROM news.digest d
    ORDER BY d.id DESC LIMIT 12
"""

SOURCES = """
    SELECT DISTINCT ON (source_id)
           source_id, ok, http_status, items_found, items_new,
           latency_ms, newest_item_age_h, error, created_at
    FROM news.source_health
    ORDER BY source_id, created_at DESC
"""

COSTS = """
    SELECT provider, model, purpose, count(*) AS calls,
           sum(input_tokens) AS tok_in, sum(output_tokens) AS tok_out,
           sum(reasoning_tokens) AS tok_nghi, round(sum(cost_usd), 5) AS usd,
           count(*) FILTER (WHERE error IS NOT NULL) AS loi
    FROM news.llm_call
    WHERE created_at > now() - interval '7 days'
    GROUP BY 1, 2, 3 ORDER BY 8 DESC NULLS LAST
"""

SIZES = """
    SELECT relname AS bang, pg_size_pretty(pg_total_relation_size(c.oid)) AS kich_thuoc
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'news' AND c.relkind = 'r'
    ORDER BY pg_total_relation_size(c.oid) DESC
"""

CSS = """
:root{--bg:#f7f7f8;--fg:#1a1a1a;--muted:#6b7280;--line:#e5e7eb;--card:#fff;
      --ok:#15803d;--warn:#b45309;--bad:#b91c1c;--accent:#1d4ed8}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--fg:#e8e8ea;--muted:#9aa0aa;
  --line:#2a2d35;--card:#191c22;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--accent:#7aa2ff}}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);
     font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:15px;margin:28px 0 10px;color:var(--muted);text-transform:uppercase;
   letter-spacing:.06em}
.sub{color:var(--muted);margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .n{font-size:22px;font-weight:600}
.card .l{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;background:var(--card);
      border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);
      font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase}
tr:last-child td{border-bottom:none}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
a{color:var(--accent)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:10px 14px;margin-bottom:8px}
summary{cursor:pointer;font-weight:600}
.item{padding:8px 0;border-bottom:1px solid var(--line)}
.item:last-child{border-bottom:none}
.item .meta{color:var(--muted);font-size:12px}
.scroll{overflow-x:auto}
@media(max-width:640px){body{padding:16px 12px 48px}th,td{padding:6px 8px}}
"""


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _status_class(status: str) -> str:
    return {"success": "ok", "sent": "ok", "partial": "warn",
            "pending": "warn", "skipped": "warn"}.get(status, "bad")


def _fmt_dt(value) -> str:
    return value.strftime("%d/%m %H:%M") if value else "-"


def _digest_items(payload: dict) -> list[tuple[str, str]]:
    """Rut tieu de + link tu card da render, de xem ngay tren trang."""
    out: list[tuple[str, str]] = []
    try:
        sections = payload["cardsV2"][0]["card"]["sections"]
    except (KeyError, IndexError, TypeError):
        return out
    for sec in sections:
        header = sec.get("header")
        if not header:
            continue
        url = ""
        for widget in sec.get("widgets", []):
            buttons = widget.get("buttonList", {}).get("buttons")
            if buttons:
                url = buttons[0].get("onClick", {}).get("openLink", {}).get("url", "")
        out.append((_TAG.sub("", header), url))
    return out


def render() -> str:
    s = get_settings()
    runs = _rows(RUNS)
    digests = _rows(DIGESTS)
    sources = _rows(SOURCES)
    costs = _rows(COSTS)
    sizes = _rows(SIZES)

    down = down_providers()
    down_note = f" · đang lỗi: {', '.join(sorted(down))}" if down else ""
    last = runs[0] if runs else {}
    bad_sources = [r for r in sources if not r["ok"]]
    stale = [r for r in sources if (r["newest_item_age_h"] or 0) > 168]
    week_cost = sum(float(c["usd"] or 0) for c in costs)

    parts: list[str] = [
        "<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='refresh' content='60'>",
        "<title>Bản tin - trạng thái</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<h1>Bản tin hằng ngày — trạng thái</h1>",
        f"<p class='sub'>{_esc(today_in(s.news_timezone).strftime('%d/%m/%Y'))} · "
        f"provider <b>{_esc(' → '.join(provider_chain()) or 'chưa cấu hình')}</b>"
        f"{down_note} · tự làm mới mỗi 60 giây</p>",
    ]

    # ---- the tong quan ----
    parts.append("<div class='cards'>")
    for label, value, cls in [
        ("Lần chạy gần nhất", _esc(last.get("status", "chưa có")),
         _status_class(last.get("status", ""))),
        ("Thời điểm", _fmt_dt(last.get("started_at")), ""),
        ("Bài mới", _esc(last.get("articles_new", 0)), ""),
        ("Tin đã chọn", _esc(last.get("articles_selected", 0)), ""),
        ("Nguồn hỏng", _esc(len(bad_sources)), "bad" if bad_sources else "ok"),
        ("Nguồn đóng băng", _esc(len(stale)), "warn" if stale else "ok"),
        ("Chi phí 7 ngày", f"${week_cost:.4f}", ""),
    ]:
        parts.append(
            f"<div class='card'><div class='n {cls}'>{value}</div>"
            f"<div class='l'>{label}</div></div>"
        )
    parts.append("</div>")

    # ---- ban tin gan day ----
    parts.append("<h2>Bản tin gần đây</h2>")
    if not digests:
        parts.append("<p class='sub'>Chưa có bản tin nào.</p>")
    for d in digests:
        items = _digest_items(d["payload"])
        cls = _status_class(d["status"])
        parts.append(
            f"<details><summary>{_esc(d['group_key'])} · "
            f"<span class='{cls}'>{_esc(d['status'])}</span> · "
            f"{_esc(d['digest_date'])} · {_esc(d['n_bai'])} tin — "
            f"{_esc(d['headline'])}</summary>"
            f"<p class='meta'>{_esc(d['overview'])}</p>"
        )
        for title, url in items:
            link = (f" <a href='{_esc(url)}' target='_blank' rel='noopener'>mở</a>"
                    if url else "")
            parts.append(f"<div class='item'>{_esc(title)}{link}</div>")
        parts.append("</details>")

    # ---- cac lan chay ----
    parts.append("<h2>Các lần chạy</h2><div class='scroll'><table><tr>"
                 "<th>Bắt đầu</th><th>Nguồn kích hoạt</th><th>Trạng thái</th>"
                 "<th>Thời lượng</th><th>Bài mới</th><th>Tin chọn</th><th>USD</th></tr>")
    for r in runs:
        parts.append(
            f"<tr><td>{_fmt_dt(r['started_at'])}</td><td>{_esc(r['trigger'])}</td>"
            f"<td class='{_status_class(r['status'])}'>{_esc(r['status'])}</td>"
            f"<td>{_esc(round((r['duration_ms'] or 0) / 1000, 1))}s</td>"
            f"<td>{_esc(r['articles_new'])}</td><td>{_esc(r['articles_selected'])}</td>"
            f"<td>{_esc(r['llm_cost_usd'])}</td></tr>"
        )
    parts.append("</table></div>")

    # ---- suc khoe nguon ----
    parts.append("<h2>Nguồn tin</h2><div class='scroll'><table><tr>"
                 "<th>Nguồn</th><th>OK</th><th>HTTP</th><th>Bài</th><th>Mới</th>"
                 "<th>Độ trễ</th><th>Bài mới nhất</th><th>Lỗi</th></tr>")
    for r in sorted(sources, key=lambda x: (x["ok"], x["source_id"])):
        age = r["newest_item_age_h"]
        age_cls = "bad" if (age or 0) > 168 else ""
        parts.append(
            f"<tr><td>{_esc(r['source_id'])}</td>"
            f"<td class='{'ok' if r['ok'] else 'bad'}'>{'có' if r['ok'] else 'KHÔNG'}</td>"
            f"<td>{_esc(r['http_status'])}</td><td>{_esc(r['items_found'])}</td>"
            f"<td>{_esc(r['items_new'])}</td><td>{_esc(r['latency_ms'])}ms</td>"
            f"<td class='{age_cls}'>{_esc(age)}h</td>"
            f"<td>{_esc((r['error'] or '')[:60])}</td></tr>"
        )
    parts.append("</table></div>")

    # ---- chi phi + dung luong ----
    parts.append("<h2>Chi phí LLM (7 ngày)</h2><div class='scroll'><table><tr>"
                 "<th>Provider</th><th>Model</th><th>Việc</th><th>Lượt</th>"
                 "<th>Lỗi</th><th>Token vào</th><th>Token ra</th>"
                 "<th>Token nghĩ</th><th>USD</th></tr>")
    for c in costs:
        err_cls = "bad" if c["loi"] else ""
        parts.append(
            f"<tr><td>{_esc(c['provider'])}</td><td>{_esc(c['model'])}</td>"
            f"<td>{_esc(c['purpose'])}</td><td>{_esc(c['calls'])}</td>"
            f"<td class='{err_cls}'>{_esc(c['loi'])}</td>"
            f"<td>{_esc(c['tok_in'])}</td><td>{_esc(c['tok_out'])}</td>"
            f"<td>{_esc(c['tok_nghi'])}</td><td>{_esc(c['usd'])}</td></tr>"
        )
    parts.append("</table></div>")

    parts.append(f"<h2>Dung lượng (giữ {s.retention_days} ngày)</h2>"
                 "<div class='scroll'><table><tr><th>Bảng</th><th>Kích thước</th></tr>")
    for row in sizes:
        parts.append(f"<tr><td>{_esc(row['bang'])}</td>"
                     f"<td>{_esc(row['kich_thuoc'])}</td></tr>")
    parts.append("</table></div>")

    parts.append("</div></body></html>")
    return "".join(parts)


class Handler(BaseHTTPRequestHandler):
    server_version = "news-bot-status"

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                body, ctype = render().encode("utf-8"), "text/html; charset=utf-8"
            elif path == "/health":
                _rows("SELECT 1 AS ok")
                body = json.dumps({"status": "ok"}).encode()
                ctype = "application/json"
            elif path == "/api/runs":
                body = json.dumps(_rows(RUNS), default=str,
                                  ensure_ascii=False).encode("utf-8")
                ctype = "application/json; charset=utf-8"
            else:
                self.send_error(404, "khong co trang nay")
                return
        except Exception as exc:  # DB sap thi trang van phai tra loi duoc
            log.warning("status.error", error=repr(exc))
            body = (f"<h1>Không đọc được dữ liệu</h1><pre>{html.escape(str(exc))}</pre>"
                    ).encode()
            ctype = "text/html; charset=utf-8"
            self.send_response(503)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        log.info("status.request", client=self.address_string(), msg=fmt % args)


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    host, port = _read_port()
    server = ThreadingHTTPServer((host, port), Handler)
    log.info("status.listening", url=f"http://{host}:{port}")
    print(f"Trang trang thai: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
