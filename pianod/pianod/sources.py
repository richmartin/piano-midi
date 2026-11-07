"""Online sheet-music sources the Scores browser searches: public domain and open licences only.

  mutopia   The Mutopia Project: engraved public-domain and CC scores, each with its LilyPond source,
            a PDF and a plain (mechanical) MIDI, all downloadable directly. Fully automatic.
  imslp     IMSLP: most of the classical piano repertoire as PDF scans. Searched here, but its file
            downloads sit behind a bot check (a CAPTCHA), which pianod doesn't get round: a result
            links to the IMSLP page, Rich downloads the PDF himself, and imports it (scores.py).

Every search returns items of one shape:

  {"source", "ref" (the source's own id), "title", "composer", "detail" (opus, year, edition),
   "licence", "formats": {"mid"|"pdf"|"ly"|"xml": url}, "page": url to open in a browser,
   "auto": True if pianod can fetch it by itself}

A source that's down or changes its pages returns [] with a warning: the browser shows what the
others found.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
import urllib.request

LOG = logging.getLogger("pianod")
UA = "pianod-score-browser/1 (a household piano; private use)"
TIMEOUT = 20


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# --- Mutopia ------------------------------------------------------------------------------
MUTOPIA = "https://www.mutopiaproject.org/cgibin/make-table.cgi"


def parse_mutopia(page: str) -> list[dict]:
    out = []
    for table in re.findall(r'<table class="table-bordered result-table">(.*?)</table>', page, re.S):
        cells = [c for c in re.findall(r"<td[^>]*>(.*?)</td>", table, re.S)]
        text = [_text(c) for c in cells]
        if len(text) < 9:
            continue
        links = dict((ext, url) for url, ext in
                     re.findall(r'href="(https://www\.mutopiaproject\.org/ftp/[^"]+?\.(ly|mid|pdf|zip))"', table))
        a4 = re.search(r'href="(https://www\.mutopiaproject\.org/ftp/[^"]+-a4\.pdf)"', table)
        if a4:
            links["pdf"] = a4.group(1)
        info = re.search(r'piece-info\.cgi\?id=(\d+)', table)
        composer = re.sub(r"^by\s+", "", text[1])
        dates = re.search(r"\(([^)]*)\)\s*$", composer)
        out.append({
            "source": "mutopia", "ref": info.group(1) if info else links.get("ly", ""),
            "title": text[0], "composer": re.sub(r"\s*\([^)]*\)\s*$", "", composer).strip(),
            "composer_dates": dates.group(1) if dates else None,
            "detail": " · ".join(t for t in (text[2], text[5], text[8]) if t and t != "\xa0"),
            "instrument": re.sub(r"^for\s+", "", text[4]),
            "licence": text[9] if len(text) > 9 else "",
            "formats": {k: v for k, v in links.items() if k in ("mid", "pdf", "ly")},
            "page": f"https://www.mutopiaproject.org/cgibin/piece-info.cgi?id={info.group(1)}" if info else MUTOPIA,
            "auto": "mid" in links or "ly" in links,
        })
    return out


def mutopia(query: str, get=_get) -> list[dict]:
    url = MUTOPIA + "?" + urllib.parse.urlencode({"searchingfor": query, "Instrument": "Piano"})
    return parse_mutopia(get(url).decode("utf-8", "replace"))


# --- IMSLP --------------------------------------------------------------------------------
IMSLP = "https://imslp.org/api.php"
_IMSLP_TITLE = re.compile(r"^(?P<work>.*) \((?P<last>[^,()]+), (?P<first>[^()]+)\)$")


def parse_imslp(data: dict) -> list[dict]:
    out = []
    for r in data.get("query", {}).get("search", []):
        m = _IMSLP_TITLE.match(r.get("title", ""))
        if not m:
            continue
        page = "https://imslp.org/wiki/" + urllib.parse.quote(r["title"].replace(" ", "_"))
        out.append({
            "source": "imslp", "ref": r["title"], "title": m["work"],
            "composer": f"{m['first']} {m['last']}".strip(), "composer_dates": None,
            "detail": "PDF scans; download on IMSLP, then import here", "instrument": "",
            "licence": "public domain (check the edition)", "formats": {}, "page": page, "auto": False,
        })
    return out


def imslp(query: str, get=_get) -> list[dict]:
    url = IMSLP + "?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query, "srnamespace": 0,
        "srlimit": 20, "format": "json"})
    return parse_imslp(json.loads(get(url)))


SOURCES = {"mutopia": mutopia, "imslp": imslp}


def search(query: str, sources=None, get=_get) -> list[dict]:
    """Search each source; automatic ones first, so what plays without help leads the list."""
    query = " ".join(query.split())[:120]
    if not query:
        return []
    out = []
    for name in sources or SOURCES:
        try:
            out += SOURCES[name](query, get=get)
        except Exception as e:                  # one source down mustn't empty the page
            LOG.warning("score search: %s: %s", name, e)
    return sorted(out, key=lambda i: not i["auto"])
