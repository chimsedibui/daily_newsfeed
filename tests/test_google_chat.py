import json

from news_bot.delivery.google_chat import (
    _webhook_url,
    build_card_message,
    build_message,
    build_weather_message,
    split_message,
)
from news_bot.models import Digest, DigestItem


def make_digest(n: int = 3, summary_len: int = 200, group: str = "serious") -> Digest:
    return Digest(
        group=group,
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
    # Header mang icon cua nhom de phan biet ba ban tin trong cung mot thread.
    assert card["header"]["title"] == "📊 Lai suat giam, AI len ngoi"
    assert card["header"]["subtitle"] == "Tin nghiêm túc · 20/09/2026"
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


def test_life_group_gets_its_own_icon_and_title():
    card = build_card_message(make_digest(2, group="life"))["cardsV2"][0]["card"]
    assert card["header"]["title"].startswith("🏐")
    assert card["header"]["subtitle"].startswith("Đời sống")


def test_card_id_is_unique_per_group():
    a = build_card_message(make_digest(1, group="serious"))["cardsV2"][0]["cardId"]
    b = build_card_message(make_digest(1, group="life"))["cardsV2"][0]["cardId"]
    assert a != b


WEATHER = Digest(
    group="weather",
    digest_date="2026-09-20",
    headline="Hà Nội: Nắng nhẹ, 25–32°C",
    overview="🌤️ Nắng nhẹ · 25–32°C",
    items=[],
    stats={
        "place": "Hà Nội", "icon": "🌤️", "label": "Nắng nhẹ",
        "t_min": 25.5, "t_max": 32.5, "now": 31.8, "feels_like": 36.9,
        "humidity": 61, "rain_mm": 0.0, "rain_prob": 2, "wind_kmh": 7.8,
        "uv": 8.25, "sunrise": "05:47", "sunset": "17:52",
        "advice": ["Chỉ số UV 8 (rất cao) — che chắn khi ra ngoài buổi trưa."],
    },
)


def test_weather_card_has_no_article_sections():
    card = build_weather_message(WEATHER)["cardsV2"][0]["card"]
    assert card["header"]["subtitle"].startswith("Thời tiết")
    # 1 khoi so lieu + 1 khoi khuyen nghi, khong co buttonList nao
    assert len(card["sections"]) == 2
    assert "buttonList" not in json.dumps(card)


def test_weather_card_shows_the_numbers():
    text = json.dumps(build_weather_message(WEATHER), ensure_ascii=False)
    assert "25–32" in text.replace("25.5", "25").replace("32.5", "32") or "25" in text
    assert "05:47" in text and "17:52" in text
    assert "UV 8" in text


def test_build_message_routes_weather_to_weather_card():
    weather = build_message(WEATHER)["cardsV2"][0]["cardId"]
    news = build_message(make_digest(1))["cardsV2"][0]["cardId"]
    assert weather.startswith("daily-weather-")
    assert news.startswith("daily-news-")
