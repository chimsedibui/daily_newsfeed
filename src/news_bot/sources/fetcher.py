"""HTTP client dung chung: retry, timeout, UA that tha, ton trong robots-friendly rate."""
from __future__ import annotations

import threading

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_settings

_local = threading.local()


def get_client() -> httpx.Client:
    """1 client / thread. http2 giup nhieu request toi cung host nhanh hon dang ke."""
    client = getattr(_local, "client", None)
    if client is None or client.is_closed:
        s = get_settings()
        client = httpx.Client(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={
                "User-Agent": s.user_agent,
                "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9",
                "Accept-Language": "vi,en;q=0.8",
            },
        )
        _local.client = client
    return client


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
def fetch(url: str, timeout: float | None = None,
          headers: dict[str, str] | None = None) -> httpx.Response:
    """GET co retry. Dung cho FEED - mat mot feed la mat ca mot nguon tin.

    4xx (tru 429) khong retry vi retry cung vo ich.
    """
    resp = get_client().get(url, timeout=timeout, headers=headers or None)
    if resp.status_code >= 500 or resp.status_code == 429:
        resp.raise_for_status()
    return resp


# Fetch fulltext la best-effort: hong thi da co lead tu RSS. Khong retry va
# timeout ngan - mot host treo tung lam ca run keo dai gan 7 phut vi 3 lan thu
# x backoff luy thua tren nhieu bai cung luc.
BEST_EFFORT_TIMEOUT = httpx.Timeout(12.0, connect=5.0)


def fetch_best_effort(url: str) -> httpx.Response:
    """GET mot lan, timeout ngan, khong retry."""
    return get_client().get(url, timeout=BEST_EFFORT_TIMEOUT)
