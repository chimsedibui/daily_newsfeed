from datetime import UTC

from news_bot.utils import canonical_url, hamming, simhash, to_signed_64


def test_canonical_url_strips_tracking_and_www():
    a = canonical_url("https://www.vnexpress.net/bai-viet-123.html?utm_source=fb&x=1#top")
    b = canonical_url("http://vnexpress.net/bai-viet-123.html?x=1")
    assert a == b == "https://vnexpress.net/bai-viet-123.html?x=1"


def test_canonical_url_drops_amp_prefix_and_trailing_slash():
    assert canonical_url("https://amp.tuoitre.vn/tin/") == "https://tuoitre.vn/tin"


def test_simhash_near_duplicates_are_close():
    a = simhash("Ngan hang Nha nuoc giam lai suat dieu hanh them 0,5 diem phan tram")
    b = simhash("NHNN giam lai suat dieu hanh them 0,5 diem phan tram tu hom nay")
    c = simhash("Doi tuyen Viet Nam thang 3-0 trong tran giao huu toi qua")
    assert hamming(a, b) < hamming(a, c)


def test_to_signed_64_fits_bigint():
    assert to_signed_64(2**64 - 1) == -1
    assert -(2**63) <= to_signed_64(simhash("bat ky chuoi nao")) < 2**63


def test_ensure_aware_localizes_naive_and_leaves_aware_alone():
    from datetime import datetime, timedelta

    from news_bot.utils import ensure_aware

    naive = datetime(2026, 9, 20, 8, 26)
    localized = ensure_aware(naive, "Asia/Ho_Chi_Minh")
    assert localized.utcoffset() == timedelta(hours=7)

    aware = datetime(2026, 9, 20, 8, 26, tzinfo=UTC)
    assert ensure_aware(aware, "Asia/Ho_Chi_Minh") == aware
    assert ensure_aware(None, "Asia/Ho_Chi_Minh") is None
