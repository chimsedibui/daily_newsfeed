"""Google Chat incoming webhook: dung cardsV2 + gui.

Gioi han cua Google Chat can nho:
  - 1 message (ke ca card) toi da ~32 KB JSON  -> ta chia nho neu vuot.
  - Webhook khong ho tro @mention thanh vien; chi @all qua text.
  - threadKey cho phep gom ban tin cung mot ngay vao 1 thread.
Tai lieu: developers.google.com/workspace/chat/quickstart/webhooks
"""
from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from .. import groups
from ..config import get_settings
from ..logging_setup import get_logger
from ..models import Digest
from ..utils import truncate

log = get_logger(__name__)

MAX_MESSAGE_BYTES = 28_000      # chua 32KB, chua cho phan Google them vao
_IMPORTANCE_ICON = {5: "🔴", 4: "🟠", 3: "🟡", 2: "⚪", 1: "⚪"}


def _widget_for_item(item) -> list[dict]:
    """Mot tin = 1 khoi decoratedText + 1 nut mo bai."""
    meta = [item.publisher]
    if item.also_at:
        meta.append("cùng đưa tin: " + ", ".join(item.also_at[:3]))
    if item.topics:
        meta.append(" · ".join(f"#{t}" for t in item.topics[:3]))

    return [
        {
            "decoratedText": {
                "topLabel": truncate(" | ".join(meta), 120),
                "text": f"<b>{_IMPORTANCE_ICON.get(item.importance, '⚪')} "
                        f"{_escape(truncate(item.headline, 120))}</b>",
                "wrapText": True,
            }
        },
        {"textParagraph": {"text": _escape(truncate(item.summary, 500))}},
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "Đọc bài",
                        "onClick": {"openLink": {"url": item.url}},
                    }
                ]
            }
        },
    ]


def _escape(text: str) -> str:
    """Google Chat card text dung subset HTML -> escape < va > cua noi dung."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _vn_date(iso_date: str) -> str:
    """2026-09-20 -> 20/09/2026. Card hien thi cho nguoi Viet doc."""
    try:
        return date.fromisoformat(iso_date).strftime("%d/%m/%Y")
    except ValueError:
        return iso_date


def build_card_message(digest: Digest) -> dict:
    """Dung 1 message cardsV2 tu Digest."""
    sections = [
        {
            "widgets": [
                {"textParagraph": {"text": _escape(digest.overview)}},
            ]
        }
    ]
    for item in digest.items:
        sections.append(
            {
                "header": f"{item.rank}. {_escape(truncate(item.headline, 80))}",
                "collapsible": False,
                "widgets": _widget_for_item(item),
            }
        )

    stats = digest.stats or {}
    sections.append(
        {
            "widgets": [
                {
                    "textParagraph": {
                        "text": (
                            f"<font color=\"#888888\">Tổng hợp từ "
                            f"{stats.get('candidates', len(digest.items))} tin ứng viên · "
                            f"chế độ biên tập: {stats.get('mode', 'n/a')}</font>"
                        )
                    }
                }
            ]
        }
    )

    group = groups.get(digest.group)
    return {
        "cardsV2": [
            {
                "cardId": f"daily-news-{digest.group}-{digest.digest_date}",
                "card": {
                    "header": {
                        "title": f"{group.icon} {truncate(digest.headline, 78)}",
                        "subtitle": f"{group.title} · {_vn_date(digest.digest_date)}",
                        "imageType": "CIRCLE",
                    },
                    "sections": sections,
                },
            }
        ]
    }


def build_weather_message(digest: Digest) -> dict:
    """Card thoi tiet: khong co danh sach tin, chi so lieu + vai dong khuyen nghi."""
    st = digest.stats or {}
    group = groups.get("weather")

    def row(label: str, value: str) -> dict:
        return {"decoratedText": {"topLabel": label, "text": value, "wrapText": True}}

    rain = f"{st.get('rain_prob', 0)}%"
    if (st.get("rain_mm") or 0) > 0:
        rain += f" · {st['rain_mm']:.1f}mm"

    facts = [
        row("Nhiệt độ", f"<b>{st.get('t_min', 0):.0f}–{st.get('t_max', 0):.0f}°C</b>"
                       + (f" · hiện {st['now']:.0f}°C" if st.get("now") is not None else "")
                       + (f" (cảm giác {st['feels_like']:.0f}°C)"
                          if st.get("feels_like") is not None else "")),
        row("Khả năng mưa", rain),
        row("Gió · Độ ẩm", f"{st.get('wind_kmh', 0):.0f} km/h"
                           + (f" · {st['humidity']:.0f}%" if st.get("humidity") is not None else "")),
        row("UV · Mặt trời", f"UV {st.get('uv', 0):.0f} · mọc {st.get('sunrise', '')} "
                             f"· lặn {st.get('sunset', '')}"),
    ]
    advice = st.get("advice") or []

    return {
        "cardsV2": [
            {
                "cardId": f"daily-weather-{digest.digest_date}",
                "card": {
                    "header": {
                        "title": f"{st.get('icon', group.icon)} {truncate(digest.headline, 78)}",
                        "subtitle": f"{group.title} · {_vn_date(digest.digest_date)}",
                        "imageType": "CIRCLE",
                    },
                    "sections": [
                        {"widgets": facts},
                        {
                            "widgets": [
                                {"textParagraph": {"text": "• " + _escape(line)}}
                                for line in advice
                            ]
                        },
                    ],
                },
            }
        ]
    }


def build_message(digest: Digest) -> dict:
    """Chon dung loai card theo nhom."""
    if digest.group == "weather":
        return build_weather_message(digest)
    return build_card_message(digest)


def split_message(payload: dict, max_bytes: int = MAX_MESSAGE_BYTES) -> list[dict]:
    """Neu message qua lon, cat theo section thanh nhieu message noi tiep."""
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= max_bytes:
        return [payload]

    card = payload["cardsV2"][0]["card"]
    sections = card["sections"]
    chunks, current = [], []
    for section in sections:
        current.append(section)
        probe = json.dumps(current, ensure_ascii=False).encode("utf-8")
        if len(probe) > max_bytes * 0.8 and len(current) > 1:
            chunks.append(current[:-1])
            current = [section]
    if current:
        chunks.append(current)

    out = []
    for i, chunk in enumerate(chunks, start=1):
        header = dict(card["header"])
        if len(chunks) > 1:
            header["subtitle"] = f"{header.get('subtitle', '')} ({i}/{len(chunks)})"
        out.append(
            {
                "cardsV2": [
                    {
                        "cardId": f"{payload['cardsV2'][0]['cardId']}-{i}",
                        "card": {"header": header, "sections": chunk},
                    }
                ]
            }
        )
    return out


def _webhook_url(base: str, thread_key: str | None) -> str:
    if not thread_key:
        return base
    parts = urlsplit(base)
    query = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
    query["threadKey"] = thread_key
    query["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def send_message(payload: dict, thread_key: str | None = None) -> list[str]:
    """Gui (co the nhieu) message. Tra ve danh sach `name` cua message da tao.

    DRY_RUN=true -> khong goi mang, chi log. Dung khi test pipeline.
    """
    s = get_settings()
    if s.dry_run:
        log.info("chat.dry_run", payload_bytes=len(json.dumps(payload, ensure_ascii=False)))
        return ["dry-run"]
    if not s.google_chat_webhook_url:
        raise RuntimeError("Chua cau hinh GOOGLE_CHAT_WEBHOOK_URL")

    url = _webhook_url(s.google_chat_webhook_url, thread_key)
    names: list[str] = []
    with httpx.Client(timeout=30.0) as client:
        for part in split_message(payload):
            resp = client.post(
                url,
                json=part,
                headers={"Content-Type": "application/json; charset=UTF-8"},
            )
            if resp.status_code >= 400:
                log.error("chat.failed", status=resp.status_code, body=resp.text[:500])
                resp.raise_for_status()
            names.append(resp.json().get("name", ""))
            log.info("chat.sent", status=resp.status_code, name=names[-1])
    return names
