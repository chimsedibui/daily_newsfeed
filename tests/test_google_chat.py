import json

from news_bot.delivery.google_chat import (
    _webhook_url,
    build_card_message,
    split_message,
)
from news_bot.models import Digest, DigestItem


def make_digest(n: int = 3, summary_len: int = 200) -> Digest:
    return Digest(
        digest_date="2026-09-20",
        headline="Lai suat giam, AI len ngoi",
        overview="Ba chu de dang chu y hom nay.",
        items=[
            DigestItem(
                rank=i,
                headline=f"Tin so {i}",
                summary="x" * summary_len,
                url=f"https://vnexpress.net/tin-{i}.html",
                publisher="VnExpress",
                topics=["kinh-te"],
                importance=4,
                also_at=["Tuoi Tre"],
            )
            for i in range(1, n + 1)
        ],
        stats={"mode": "llm", "candidates": 24},
    )


def test_card_has_header_and_one_section_per_item():
    payload = build_card_message(make_digest(3))
    card = payload["cardsV2"][0]["card"]
    assert card["header"]["title"] == "Lai suat giam, AI len ngoi"
    # 1 section overview + 3 section tin + 1 section footer
    assert len(card["sections"]) == 5
    assert card["sections"][1]["header"].startswith("1. ")


def test_card_escapes_html_in_content():
    digest = make_digest(1)
    digest.items[0].summary = 'Gia <b>tang</b> & "manh"'
    payload = build_card_message(digest)
    text = json.dumps(payload, ensure_ascii=False)
    assert "&lt;b&gt;" in text
    assert "<b>tang</b>" not in text


def test_item_widget_carries_a_working_link():
    payload = build_card_message(make_digest(1))
    widgets = payload["cardsV2"][0]["card"]["sections"][1]["widgets"]
    button = widgets[-1]["buttonList"]["buttons"][0]
    assert button["onClick"]["openLink"]["url"] == "https://vnexpress.net/tin-1.html"


def test_split_message_keeps_small_payload_intact():
    assert len(split_message(build_card_message(make_digest(3)))) == 1


def test_split_message_chunks_oversized_payload():
    huge = build_card_message(make_digest(40, summary_len=500))
    parts = split_message(huge, max_bytes=8000)
    assert len(parts) > 1
    for part in parts:
        assert len(json.dumps(part, ensure_ascii=False).encode()) < 12000
    assert "(1/" in parts[0]["cardsV2"][0]["card"]["header"]["subtitle"]


def test_webhook_url_appends_thread_key_without_losing_auth_params():
    base = "https://chat.googleapis.com/v1/spaces/AAA/messages?key=K&token=T"
    url = _webhook_url(base, "daily-news-2026-09-20")
    assert "key=K" in url and "token=T" in url
    assert "threadKey=daily-news-2026-09-20" in url
    assert "messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD" in url


def test_webhook_url_unchanged_when_no_thread_key():
    base = "https://chat.googleapis.com/v1/spaces/AAA/messages?key=K"
    assert _webhook_url(base, None) == base
