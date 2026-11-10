"""Article ingestion for the News recipe.

Two inputs are accepted:

  * a URL — fetched, stripped of scripts/navigation/ads, and reduced to the
    readable body paragraphs;
  * pasted article text — used as-is.

Scraping the open web is inherently uneven, so every failure raises a clear,
recoverable ``ArticleError`` and the UI always offers "paste the text" as the
fallback. No third-party parsing dependency is needed for the first version:
stdlib ``urllib`` + ``HTMLParser`` cover the common article sites, and the
recipe still validates whatever text it gets.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

MAX_BYTES = 2_500_000
MAX_TEXT_CHARS = 14_000
_TIMEOUT = 25
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 VidyraArticleBot/1.0"
)

# Containers whose text is navigation/chrome, never the article body.
_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "nav", "header", "footer", "aside",
    "form", "button", "iframe", "template",
}

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_BARE_URL_RE = re.compile(
    r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?$", re.IGNORECASE
)


class ArticleError(RuntimeError):
    pass


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []
        self._current: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            data = {k.lower(): (v or "") for k, v in attrs}
            name = (data.get("name") or data.get("property") or "").lower()
            if name in ("description", "og:description") and not self.description:
                self.description = html.unescape(data.get("content") or "").strip()
            return
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "blockquote", "article"):
            self._flush()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in ("p", "li", "h1", "h2", "h3", "blockquote", "article", "div"):
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        self._current.append(data)

    def _flush(self):
        text = _WS_RE.sub(" ", "".join(self._current)).strip()
        self._current = []
        if text:
            self._chunks.append(text)

    def close(self):
        super().close()
        self._flush()


def _clean_text(text: str) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]
    # Single short lines are usually captions/byline furniture, not prose.
    body = [p for p in paragraphs if len(p) >= 40]
    if not body and paragraphs:
        body = paragraphs
    merged = "\n\n".join(body)
    merged = _BLANK_RE.sub("\n\n", merged)
    return merged.strip()


def _assert_public(url: str) -> None:
    """Block SSRF: only public web hosts on standard ports may be fetched."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ArticleError("Only http(s) links can be fetched. Paste the article text instead.")
    if parsed.port not in (None, 80, 443):
        raise ArticleError("Only standard web links (ports 80/443) can be fetched.")
    host = parsed.hostname or ""
    if host.lower() == "localhost":
        raise ArticleError("That link points somewhere private. Paste the article text instead.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ArticleError("Could not resolve that link's host. Paste the article text instead.")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ArticleError(
                "That link points to a private address. Paste the article text instead."
            )


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str) -> tuple[str, str]:
    """Return (html, final_url)."""
    _assert_public(url)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*"})
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(_SafeRedirect(), urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(req, timeout=_TIMEOUT) as resp:
            final_url = resp.geturl() or url
            raw = resp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raw = raw[:MAX_BYTES]
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise ArticleError(f"That link returned HTTP {exc.code}. Paste the article text instead.")
    except urllib.error.URLError as exc:
        raise ArticleError(f"Could not reach that link ({exc.reason}). Paste the article text instead.")
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a fallback hint
        raise ArticleError(f"Could not read that link ({exc}). Paste the article text instead.")
    try:
        return raw.decode(charset, errors="replace"), final_url
    except LookupError:
        return raw.decode("utf-8", errors="replace"), final_url


def _site_name(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def extract(source: str, title_hint: str = "") -> dict:
    """Turn a URL or pasted text into ``{title, text, url, site, word_count}``."""
    source = (source or "").strip()
    if not source:
        raise ArticleError("Paste an article link or the article text.")

    scheme = _SCHEME_RE.match(source)
    if scheme:
        if not source.lower().startswith(("http://", "https://")):
            raise ArticleError("Only http(s) links can be fetched. Paste the article text instead.")
        target = source
    elif _BARE_URL_RE.match(source):
        target = "https://" + source
    else:
        target = ""

    if target:
        page, final_url = _fetch(target)
        parser = _Extractor()
        try:
            parser.feed(page)
            parser.close()
        except Exception:
            pass
        title = (title_hint or parser.title or "").strip()
        text = _clean_text("\n\n".join(parser._chunks))
        if not text:
            text = _clean_text(parser.description)
        if len(text.split()) < 60:
            raise ArticleError(
                "Couldn't find enough article text at that link (it may be paywalled or "
                "JavaScript-rendered). Paste the article text instead."
            )
        url = final_url
        site = _site_name(url)
    else:
        text = source
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        title = (title_hint or (lines[0][:120] if lines else "News video")).strip()
        # Drop the title line from the body only when it was clearly a headline.
        if len(lines) > 1 and lines[0] == title:
            text = "\n".join(lines[1:])
        url = ""
        site = "pasted"

    truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS].strip()
    return {
        "title": title[:160],
        "text": text,
        "url": url,
        "site": site,
        "word_count": len(text.split()),
        "truncated": truncated,
    }
