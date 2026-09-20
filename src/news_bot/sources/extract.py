"""Trich xuat noi dung tu trang bai viet SSR.

Chien luoc 3 tang, dung chung cho moi bao VN (da kiem chung tren VnExpress,
Tuoi Tre, Thanh Nien, Dan Tri, VietnamNet, CafeF):

  1. JSON-LD schema.org NewsArticle  <- chinh xac nhat, co datePublished/author
  2. the <meta> og:* / article:*     <- fallback khi thieu JSON-LD
  3. heuristic DOM                   <- gom <p> trong khoi bai viet

Khong dung selector rieng cho tung bao: selector rieng la thu vo dau tien
hong khi bao doi giao dien.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

_ARTICLE_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting"}
_BLOCK_TAGS = ("script", "style", "noscript", "iframe", "figure", "figcaption",
               "aside", "form")
_WS = re.compile(r"[ \t\xa0]+")
_NL = re.compile(r"\n{3,}")


def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        # JSON-LD co the la object, list, hoac @graph
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node:
                    stack.append(node["@graph"])
                yield node


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return dateparser.parse(value)
    except (ValueError, OverflowError):
        return None


def _flatten_author(author: Any) -> str | None:
    if isinstance(author, dict):
        return author.get("name")
    if isinstance(author, list) and author:
        names = [a.get("name") for a in author if isinstance(a, dict) and a.get("name")]
        return ", ".join(names) or None
    if isinstance(author, str):
        return author
    return None


def _clean(text: str) -> str:
    # JSON-LD cua nhieu bao (CafeF, VietnamNet) nhet entity da escape hai lan
    # ("&amp;quot;") -> phai unescape lap cho den khi on dinh.
    for _ in range(3):
        unescaped = html_mod.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = _WS.sub(" ", text)
    return _NL.sub("\n\n", text).strip()


def _body_from_dom(soup: BeautifulSoup) -> str:
    """Gom cac doan van dai trong khoi co nhieu <p> nhat - du chinh xac cho bao VN."""
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.decompose()

    best_score, best_text = 0, ""
    for container in soup.find_all(["article", "div", "section"], limit=400):
        paragraphs = container.find_all("p", recursive=False) or container.find_all("p")
        if len(paragraphs) < 3:
            continue
        texts = [p.get_text(" ", strip=True) for p in paragraphs]
        texts = [t for t in texts if len(t) > 40]
        score = sum(len(t) for t in texts)
        if score > best_score:
            best_score, best_text = score, "\n\n".join(texts)
    return _clean(best_text)


def extract_article(html: str, url: str) -> dict[str, Any]:
    """Tra ve dict: title, lead, body, author, published_at, image_url, lang."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, Any] = {
        "title": None, "lead": None, "body": None, "author": None,
        "published_at": None, "image_url": None, "lang": "vi", "source_kind": None,
    }

    for node in _iter_jsonld(soup):
        types = node.get("@type")
        types = {types} if isinstance(types, str) else set(types or [])
        if not types & _ARTICLE_TYPES:
            continue
        headline = node.get("headline")
        out["title"] = _clean(headline) if headline else out["title"]
        description = node.get("description")
        out["lead"] = _clean(description) if description else out["lead"]
        out["author"] = _flatten_author(node.get("author")) or out["author"]
        out["published_at"] = _parse_dt(node.get("datePublished")) or out["published_at"]
        image = node.get("image") or node.get("thumbnailUrl")
        if isinstance(image, dict):
            image = image.get("url")
        if isinstance(image, list) and image:
            image = image[0].get("url") if isinstance(image[0], dict) else image[0]
        out["image_url"] = image or out["image_url"]
        body = node.get("articleBody")
        if isinstance(body, str) and len(body) > 200:
            out["body"] = _clean(body)
        out["source_kind"] = "json-ld"
        break

    def meta(*names: str) -> str | None:
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find(
                "meta", attrs={"name": n}
            )
            if tag and tag.get("content"):
                return tag["content"].strip()
        return None

    fallback_title = meta("og:title", "twitter:title") or (
        soup.title.get_text(strip=True) if soup.title else None
    )
    out["title"] = out["title"] or (_clean(fallback_title) if fallback_title else None)
    fallback_lead = meta("og:description", "description")
    out["lead"] = out["lead"] or (_clean(fallback_lead) if fallback_lead else None)
    out["image_url"] = out["image_url"] or meta("og:image")
    out["published_at"] = out["published_at"] or _parse_dt(
        meta("article:published_time", "pubdate", "date")
    )
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        out["lang"] = html_tag["lang"][:2]
    if out["source_kind"] is None and out["title"]:
        out["source_kind"] = "meta"

    if not out["body"]:
        out["body"] = _body_from_dom(soup) or None
        if out["body"]:
            out["source_kind"] = (out["source_kind"] or "dom") + "+dom"

    return out
