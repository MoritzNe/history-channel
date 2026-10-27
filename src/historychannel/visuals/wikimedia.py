"""Wikimedia Commons client: exact-title fetch or search, free licenses only, cached on disk.

Cache layout (CACHE_DIR / "wikimedia"):
    files/<sha1>.jpg|png + files/<sha1>.json   downloaded thumbnail + metadata
    search/<sha1>.json                         query -> ranked candidate titles
Reruns never hit the network for anything already cached.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from historychannel import CACHE_DIR

log = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "HistoryChannelBot/0.1 (https://github.com/MoritzNe/history-channel) python-requests"
THUMB_WIDTH = 2560
MIN_GOOD_WIDTH = 1200
BITMAP_MIMES = {"image/jpeg", "image/png", "image/tiff", "image/webp"}
EXTMETA = "LicenseShortName|License|Artist|Credit|UsageTerms|ObjectName|DateTimeOriginal|LicenseUrl|NonFree|Restrictions"

_FREE_RE = re.compile(r"public domain|^pd\b|^pd-|cc0|cc[- ]by|cc[- ]sa|attribution|no restrictions|gfdl|free art", re.I)
_NONFREE_RE = re.compile(r"\bnc\b|-nc|non-?commercial|\bnd\b|-nd|no ?deriv|fair use|non-?free|copyrighted", re.I)


class CommonsError(RuntimeError):
    pass


@dataclass
class CommonsImage:
    title: str
    path: Path
    width: int
    height: int
    license: str
    artist: str | None
    credit: str | None
    page_url: str
    object_name: str | None = None
    date: str | None = None

    @property
    def attribution(self) -> str:
        parts = []
        if self.artist and self.artist.lower() not in {"unknown", "unknown author", "anonymous"}:
            parts.append(self.artist)
        name = self.object_name or self.title.removeprefix("File:").rsplit(".", 1)[0]
        parts.append(f"“{name}”")
        parts.append(self.license)
        return ", ".join(parts) + " — via Wikimedia Commons"


def clean_html(s: str | None, max_len: int = 120) -> str | None:
    """Strip tags/entities from Commons extmetadata (Artist etc.) and squash whitespace."""
    if not s:
        return None
    s = re.sub(r"<br\s*/?>", ", ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", html.unescape(s)).strip(" ,;")
    if len(s) > max_len:
        s = s[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return s or None


def is_free_license(short: str | None, code: str | None = None, nonfree: str | None = None) -> bool:
    text = " ".join(x for x in (short, code) if x)
    if not text or (nonfree and nonfree.strip().lower() in {"true", "1"}):
        return False
    if _NONFREE_RE.search(text):
        return False
    return bool(_FREE_RE.search(text))


def _key(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:20]


def _norm_title(title: str) -> str:
    title = title.strip().replace("_", " ")
    return title if title.lower().startswith("file:") else f"File:{title}"


class CommonsClient:
    def __init__(self, cache_dir: Path | None = None, session: requests.Session | None = None):
        self.cache = Path(cache_dir or CACHE_DIR / "wikimedia")
        (self.cache / "files").mkdir(parents=True, exist_ok=True)
        (self.cache / "search").mkdir(parents=True, exist_ok=True)
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._last = 0.0

    # ------------------------------------------------------------------ http
    def _get(self, url: str, params: dict | None = None, *, stream=False, tries: int = 5) -> requests.Response:
        delay = 1.0
        for attempt in range(tries):
            wait = 0.25 - (time.monotonic() - self._last)  # serial, <= ~4 req/s
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.get(url, params=params, timeout=(10, 60), stream=stream)
            except requests.RequestException as e:
                if attempt == tries - 1:
                    raise CommonsError(f"network error: {e}") from e
            else:
                if r.status_code == 200:
                    return r
                if r.status_code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                    raise CommonsError(f"HTTP {r.status_code} for {r.url}")
                ra = r.headers.get("Retry-After")
                delay = float(ra) if ra and ra.isdigit() else delay
            time.sleep(min(delay, 30))
            delay *= 2
        raise CommonsError("unreachable")

    def _api(self, **params) -> dict:
        params = {"action": "query", "format": "json", "formatversion": "2", "maxlag": "5", **params}
        data = self._get(API, params).json()
        if "error" in data:
            raise CommonsError(str(data["error"]))
        return data

    # ------------------------------------------------------------------ metadata
    def _info_params(self) -> dict:
        return {
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": str(THUMB_WIDTH),
            "iiextmetadatafilter": EXTMETA,
        }

    @staticmethod
    def _candidate(page: dict) -> dict | None:
        ii = (page.get("imageinfo") or [None])[0]
        if not ii or ii.get("mime") not in BITMAP_MIMES:
            return None
        em = {k: (v or {}).get("value") for k, v in (ii.get("extmetadata") or {}).items()}
        lic = clean_html(em.get("LicenseShortName")) or ""
        if not is_free_license(lic, em.get("License"), em.get("NonFree")):
            return None
        return {
            "title": page["title"],
            "width": ii.get("width", 0),
            "height": ii.get("height", 0),
            "thumb_url": ii.get("thumburl") or ii.get("url"),
            "page_url": ii.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{page['title']}",
            "license": lic,
            "artist": clean_html(em.get("Artist")),
            "credit": clean_html(em.get("Credit")),
            "object_name": clean_html(em.get("ObjectName")),
            "date": clean_html(em.get("DateTimeOriginal"), 40),
            "rank": page.get("index", 0),
        }

    @staticmethod
    def score(c: dict) -> float:
        """Higher is better: search rank first, then landscape and resolution bonuses."""
        w, h = c["width"] or 1, c["height"] or 1
        s = -float(c.get("rank", 0))
        s += 3.0 if w >= MIN_GOOD_WIDTH else (1.0 if w >= 800 else -4.0)
        aspect = w / h
        s += 2.0 if 1.25 <= aspect <= 2.1 else (0.5 if aspect >= 0.95 else 0.0)
        return s

    # ------------------------------------------------------------------ public
    def fetch(self, title: str) -> CommonsImage:
        """Fetch one file by exact Commons title (with or without the 'File:' prefix)."""
        title = _norm_title(title)
        cached = self._load_cached(title)
        if cached:
            return cached
        data = self._api(titles=title, redirects="1", **self._info_params())
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            raise CommonsError(f"no such file: {title}")
        cand = self._candidate(pages[0])
        if not cand:
            raise CommonsError(f"{title}: not a bitmap or not freely licensed")
        return self._download(cand, alias=title)

    def search(self, query: str, limit: int = 12) -> list[dict]:
        """Ranked, license-filtered candidates for a free-text query (cached)."""
        cpath = self.cache / "search" / f"{_key(query)}.json"
        if cpath.exists():
            return json.loads(cpath.read_text())
        data = self._api(
            generator="search",
            gsrsearch=f"{query} filetype:bitmap",
            gsrnamespace="6",
            gsrlimit=str(limit),
            **self._info_params(),
        )
        pages = data.get("query", {}).get("pages", [])
        cands = [c for c in (self._candidate(p) for p in pages) if c]
        cands.sort(key=self.score, reverse=True)
        cpath.write_text(json.dumps(cands, indent=1))
        return cands

    def search_best(self, query: str, *, exclude: set[str] | None = None) -> CommonsImage:
        exclude = exclude or set()
        last: Exception | None = None
        for cand in self.search(query):
            if cand["title"] in exclude:
                continue
            try:
                return self._load_cached(cand["title"]) or self._download(cand)
            except CommonsError as e:  # try the next candidate
                last = e
        raise CommonsError(f"no usable free image for query {query!r}" + (f" ({last})" if last else ""))

    # ------------------------------------------------------------------ cache
    def _paths(self, title: str) -> tuple[Path, Path]:
        k = _key(title)
        return self.cache / "files" / f"{k}.json", self.cache / "files" / k

    def _load_cached(self, title: str) -> CommonsImage | None:
        meta_p, _ = self._paths(title)
        if not meta_p.exists():
            return None
        meta = json.loads(meta_p.read_text())
        meta["path"] = Path(meta["path"])
        if not meta["path"].exists():
            return None
        return CommonsImage(**meta)

    def _download(self, cand: dict, alias: str | None = None) -> CommonsImage:
        meta_p, stem = self._paths(cand["title"])
        url = cand["thumb_url"]
        ext = ".png" if url.lower().split("?")[0].endswith(".png") else ".jpg"
        img_p = stem.with_suffix(ext)
        r = self._get(url, stream=True)
        tmp = img_p.with_suffix(ext + ".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        tmp.replace(img_p)
        img = CommonsImage(
            title=cand["title"],
            path=img_p,
            width=cand["width"],
            height=cand["height"],
            license=cand["license"],
            artist=cand["artist"],
            credit=cand["credit"],
            page_url=cand["page_url"],
            object_name=cand.get("object_name"),
            date=cand.get("date"),
        )
        meta = {**asdict(img), "path": str(img_p)}
        meta_p.write_text(json.dumps(meta, indent=1))
        if alias and alias != cand["title"]:  # redirect: cache under the requested name too
            self._paths(alias)[0].write_text(json.dumps(meta, indent=1))
        return img
