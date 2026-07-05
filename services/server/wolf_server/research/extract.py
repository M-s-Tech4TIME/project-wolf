"""Readable-text extraction from fetched HTML — stdlib only (ADR 0032 A1).

Turns a page into what the model actually needs: the title, the readable
body text (boilerplate stripped), and the in-page links (for the bounded
crawler). Built on `html.parser.HTMLParser` — no lxml/bs4 dependency, per
the lean-wheels posture (ADR 0007): Wolf reads docs pages, not arbitrary
web apps, and a tolerant best-effort parse is exactly right for evidence
text. Parsing is pure Python over an already size-capped body (the fetcher
enforces the decompressed cap BEFORE this runs), so there is no bomb
surface here.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

# Content inside these elements is never readable text.
_SKIP_ELEMENTS = frozenset(
    {"script", "style", "noscript", "template", "svg", "iframe", "canvas", "object"}
)
# Page chrome: navigation, headers/footers, sidebars, forms. Dropping them
# keeps doc-page extraction focused on the article body.
_CHROME_ELEMENTS = frozenset({"nav", "header", "footer", "aside", "form"})
# Elements whose boundaries imply a line break in the extracted text.
_BLOCK_ELEMENTS = frozenset(
    {
        "p", "div", "section", "article", "main", "br", "hr", "li", "ul", "ol",
        "table", "tr", "th", "td", "h1", "h2", "h3", "h4", "h5", "h6",
        "pre", "blockquote", "dt", "dd", "figure", "figcaption", "details", "summary",
    }
)  # fmt: skip

_COLLAPSE_SPACES = re.compile(r"[ \t\f\v]+")
_COLLAPSE_NEWLINES = re.compile(r"\n{3,}")
# Strip control characters (keep \n and \t) from text destined for the model
# or a log line — log-forging defense (ADR 0032 A6 §12).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    """Remove control characters that could forge log lines or break rendering."""
    return _CONTROL_CHARS.sub("", text)


@dataclass
class ExtractedPage:
    """What extraction yields from one HTML document."""

    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)


class _Extractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._skip_depth = 0
        self._chrome_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._links: list[str] = []
        self._seen_links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_ELEMENTS:
            self._skip_depth += 1
            return
        if tag in _CHROME_ELEMENTS:
            self._chrome_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_ELEMENTS:
            self._text_parts.append("\n")
        if tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), None)
            if href:
                self._add_link(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_ELEMENTS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _CHROME_ELEMENTS:
            self._chrome_depth = max(0, self._chrome_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_ELEMENTS:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._chrome_depth:
            return
        if data.strip():
            self._text_parts.append(data)

    def _add_link(self, href: str) -> None:
        absolute, _fragment = urldefrag(urljoin(self._base_url, href))
        if absolute.startswith(("http://", "https://")) and absolute not in self._seen_links:
            self._seen_links.add(absolute)
            self._links.append(absolute)

    def result(self) -> ExtractedPage:
        raw = "".join(self._text_parts)
        text = _COLLAPSE_SPACES.sub(" ", raw)
        text = "\n".join(line.strip() for line in text.split("\n"))
        text = _COLLAPSE_NEWLINES.sub("\n\n", text).strip()
        title = _COLLAPSE_SPACES.sub(" ", "".join(self._title_parts)).strip()
        return ExtractedPage(
            title=sanitize_text(title),
            text=sanitize_text(text),
            links=self._links,
        )


def extract_html(html: str, *, base_url: str) -> ExtractedPage:
    """Extract title, readable text, and absolute in-page links from HTML.

    Best-effort and tolerant: HTMLParser never raises on malformed markup,
    which is the right posture for real-world pages — a broken page yields
    whatever text it can, never an exception on the fetch path.
    """
    parser = _Extractor(base_url)
    parser.feed(html)
    parser.close()
    return parser.result()
