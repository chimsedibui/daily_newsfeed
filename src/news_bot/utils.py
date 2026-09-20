"""Tiện ích thuần: canonical URL, hash, simhash, chuẩn hoá text tiếng Việt."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "gclid", "fbclid", "zarsrc", "vn_source", "vn_campaign", "ref")
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def canonical_url(url: str) -> str:
    """Bỏ tracking param, chuẩn hoá scheme/host, bỏ fragment & trailing slash.

    Mục đích: cùng một bài share từ nhiều nơi phải quy về 1 khoá duy nhất
    để UNIQUE constraint trong Postgres làm việc dedupe cấp 1.
    """
    parts = urlsplit(url.strip())
    scheme = "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.startswith("amp."):
        netloc = netloc[4:]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(sorted(query)), ""))


def normalize_text(text: str) -> str:
    """Hạ dấu + bỏ dấu câu, dùng cho so khớp gần đúng (không dùng để hiển thị)."""
    text = unicodedata.normalize("NFC", text or "").lower()
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def content_hash(*parts: str | None) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(normalize_text(p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _shingles(tokens: list[str], n: int = 2) -> list[str]:
    if len(tokens) < n:
        return tokens
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def simhash(text: str, bits: int = 64) -> int:
    """SimHash 64-bit trên 2-gram. Không cần thư viện ngoài.

    Hai bài cùng sự kiện nhưng khác báo thường lệch < 12 bit.
    """
    tokens = normalize_text(text).split()
    feats = _shingles(tokens, 2) or tokens
    if not feats:
        return 0
    vector = [0] * bits
    for f in feats:
        hv = int.from_bytes(hashlib.blake2b(f.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(bits):
            vector[i] += 1 if (hv >> i) & 1 else -1
    out = 0
    for i, v in enumerate(vector):
        if v > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def to_signed_64(value: int) -> int:
    """Postgres BIGINT là signed; simhash là unsigned 64-bit."""
    return value - (1 << 64) if value >= (1 << 63) else value


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
