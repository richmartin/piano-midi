"""Online sheet-music sources the Scores browser searches: public domain and open licences only.

  openscore OpenScore Lieder: ~1,300 songs for voice and piano as MusicXML (.mxl), CC0, on GitHub.
            MusicXML needs no reading from a page, and Saxy confirms the piano part itself (the
            voice shows on Saxy's stand): the best source there is. Searched in a list of its files,
            fetched from GitHub once a day.
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
import time
import unicodedata
import urllib.parse
import urllib.request

LOG = logging.getLogger("pianod")
UA = "pianod-score-browser/1 (a household piano; private use)"
TIMEOUT = 20


def safe_url(url: str) -> str:
    """Percent-encode what isn't ASCII (OpenScore's paths have umlauts: "Ständchen"), leaving what's
    already encoded alone."""
    return urllib.parse.quote(url, safe=":/?&=%#,+;@!$'()*[]~")


def _get(url: str) -> bytes:
    req = urllib.request.Request(safe_url(url), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# --- OpenScore Lieder ----------------------------------------------------------------------
OPENSCORE_TREE = "https://api.github.com/repos/OpenScore/Lieder/git/trees/main?recursive=1"
OPENSCORE_RAW = "https://raw.githubusercontent.com/OpenScore/Lieder/main/"
OPENSCORE_PAGE = "https://github.com/OpenScore/Lieder/tree/main/"
TREE_TTL = 24 * 3600
_tree = {"at": 0.0, "songs": []}


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()


def openscore_songs(tree: dict) -> list[dict]:
    """scores/<Family,_First>/<Set or _>/<Song>/lc123.mxl -> one song each."""
    out = []
    for t in tree.get("tree", []):
        m = re.fullmatch(r"scores/([^/]+)/([^/]+)/([^/]+)/(lc\d+)\.mxl", t.get("path", ""))
        if not m:
            continue
        comp, coll, song, lc = (urllib.parse.unquote(x).replace("_", " ").strip() for x in m.groups())
        if "," in comp:
            fam, first = (x.strip() for x in comp.split(",", 1))
            comp = f"{first} {fam}"
        out.append({"source": "openscore", "ref": lc, "title": song, "composer": comp,
                    "composer_dates": None, "detail": "" if coll in ("", "_") else coll,
                    "instrument": "Voice and piano", "licence": "CC0 (public domain)",
                    "formats": {"mxl": OPENSCORE_RAW + t["path"]},
                    "page": OPENSCORE_PAGE + t["path"].rsplit("/", 1)[0], "auto": True,
                    "_text": _fold(f"{comp} {coll} {song}")})
    return out


def openscore(query: str, get=_get, now=time.time) -> list[dict]:
    if not _tree["songs"] or now() - _tree["at"] > TREE_TTL:
        _tree["songs"] = openscore_songs(json.loads(get(OPENSCORE_TREE)))
        _tree["at"] = now()
    words = _fold(query).split()
    hits = [s for s in _tree["songs"] if all(w in s["_text"] for w in words)]
    return [{k: v for k, v in s.items() if k != "_text"} for s in hits[:40]]


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
            # the opus is a column of its own: "Nocturne" alone is one of twenty
            "title": f"{text[0]}, {text[2]}" if text[2] and text[2] != "\xa0" and text[2] not in text[0] else text[0],
            "composer": re.sub(r"\s*\([^)]*\)\s*$", "", composer).strip(),
            "composer_dates": dates.group(1) if dates else None,
            "detail": " · ".join(t for t in (text[5], text[8]) if t and t != "\xa0"),
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


SOURCES = {"openscore": openscore, "mutopia": mutopia, "imslp": imslp}


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
