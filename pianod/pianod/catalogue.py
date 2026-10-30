"""Tidy the MIDI library's catalogue: library.json v1 (or v2) in, v2 out.

v1 is what the scraper produced: composers under whatever spelling the source used
("Mndlsn", "Albeniz, Isaac"), a 147-work "Unknown Composer" made of piano-roll titles that
carry the composer and pianist inside them ("Sonata No.15 (Mozart)-1, Landowska DA"), and
transcriptions filed under both composer and transcriber.

v2 keeps every v1 field and adds canonical composers (name, short, first, sort, dates, era),
clean work titles with movement / performer / roll / duration split out, and a pianists list.
Each work id appears exactly once. tidy() is pure and idempotent: a v2 input is re-derived
from each work's `original` title, so tidy(tidy(x)) == tidy(x).

The knowledge (who is who, spellings, dates, hand fixes) is in catalogue_data; this module
holds only the rules.
"""

from __future__ import annotations

import re
import unicodedata

from . import catalogue_data as D

UNKNOWN = "unknown"
_UNKNOWN_COMPOSER = dict(name="Unknown composer", first="", short="Unknown", born=None, died=None,
                         era=None, sort="~unknown", aliases=["Unknown Composer", "Unknown", ""])
_ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


# ---------------------------------------------------------------- names

def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _key(s: str) -> str:
    """Alias match key: accents, case, spaces and punctuation don't count."""
    return re.sub(r"[^a-z0-9]", "", ascii_fold(s).lower())


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ascii_fold(s).lower()).strip("-") or "x"


def _family(name: str, first: str) -> str:
    return name[len(first):].strip() if first and name.startswith(first) else name


def _build_index(table: dict, auto_family: bool) -> dict:
    index: dict[str, str] = {}
    families: dict[str, set] = {}
    for slug, p in table.items():
        fam = _family(p["name"], p.get("first", ""))
        names = [p["name"], slug, *p.get("aliases", [])]
        if p.get("first"):
            names.append(f"{fam}, {p['first']}")
        for n in names:
            index.setdefault(_key(n), slug)
        families.setdefault(_key(fam.split()[-1]), set()).add(slug)
    if auto_family:                       # bare family name, when it is unambiguous
        for k, slugs in families.items():
            if len(slugs) == 1:
                index.setdefault(k, next(iter(slugs)))
    return index


_COMPOSERS = {**D.COMPOSERS, UNKNOWN: _UNKNOWN_COMPOSER}
_COMPOSER_INDEX = _build_index(_COMPOSERS, auto_family=True)
_PIANIST_INDEX = _build_index(D.PIANISTS, auto_family=False)
# Pianist spellings to look for at the end of a roll title, longest first.
_PIANIST_TAILS = sorted({a for p in D.PIANISTS.values() for a in [p["short"], *p["aliases"]]}
                        | {p["name"] for p in D.PIANISTS.values()}, key=len, reverse=True)
_NICKNAMES = {_key(k): v for k, v in D.NICKNAMES.items()}


def find_composer(name: str) -> str | None:
    return _COMPOSER_INDEX.get(_key(name))


def _synth_composer(raw: str) -> dict:
    """A composer the data table doesn't know: make the best of the v1 spelling."""
    raw = re.sub(r"\s*\(.*?\)\s*", " ", raw).strip()
    if "," in raw:
        fam, first = (x.strip() for x in raw.split(",", 1))
    else:
        parts = raw.split()
        fam, first = parts[-1], " ".join(parts[:-1])
    return dict(name=f"{first} {fam}".strip(), first=first, short=fam, born=None, died=None,
                era=None, aliases=[raw])


def _composer_entry(slug: str, info: dict) -> dict:
    fam = _family(info["name"], info.get("first", ""))
    sort = info.get("sort") or ascii_fold(f"{fam} {info.get('first', '')}").lower().strip()
    return {"name": info["name"], "slug": slug, "short": info.get("short") or fam,
            "first": info.get("first", ""), "sort": sort,
            "born": info.get("born"), "died": info.get("died"), "era": info.get("era")}


# ---------------------------------------------------------------- titles

def roman(nums: list[int]) -> str | None:
    nums = [n for n in nums if 0 < n < len(_ROMAN)]
    if not nums:
        return None
    if len(nums) > 1 and nums == list(range(nums[0], nums[0] + len(nums))):
        return f"{_ROMAN[nums[0]]}–{_ROMAN[nums[-1]]}"
    return ", ".join(_ROMAN[n] for n in nums)


def _numbers(s: str) -> list[int]:
    """'2,3' / '2, 3' / '1-2' -> [2, 3] / [2, 3] / [1, 2]."""
    s = s.strip()
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if m:
        a, b = int(m[1]), int(m[2])
        return list(range(a, b + 1)) if a < b <= a + 12 else [a, b]
    return [int(x) for x in re.findall(r"\d+", s)]


def _key_name(k: str) -> str:
    """'Bb' -> 'B-flat', 'C# minor' -> 'C-sharp minor', 'f' -> 'F minor'."""
    m = re.fullmatch(r"([A-Ga-g])([b#]?)(?:\s+(minor|major))?", k.strip())
    if not m:
        return k
    note = m[1].upper() + {"b": "-flat", "#": "-sharp", "": ""}[m[2]]
    mode = m[3] or ("minor" if m[1].islower() else "")
    return f"{note} {mode}".strip()


def _strip_roll(t: str):
    """Trailing ', Pianist CODE' -> (rest, pianist slug|None, roll|None, raw unknown pianist)."""
    roll = None
    m = re.search(r"[\s,]+(DA|AA|AB|RW|LW|GW|A)$", t)
    if m:
        roll, t = D.ROLLS[m[1]], t[:m.start()]
    for alias in _PIANIST_TAILS:
        if t.endswith(alias):
            before = t[:-len(alias)]
            sep = re.search(r"(,\s*|\)\s+|\s+)$", before)
            # without a roll code, a pianist needs a clear separator in front
            if sep and (roll or sep[1].strip() in (",", ")")) and \
                    (roll or not re.search(r"\d,\s*$", before)):
                return before.rstrip(" ,"), _PIANIST_INDEX[_key(alias)], roll, None
    if roll:
        m = re.search(r",\s*([A-Z][A-Za-z'\-]+)$", t)
        if m:  # a pianist the table doesn't know: keep the name, flag it
            return t[:m.start()], None, roll, m[1]
    return t, None, roll, None


def _parens_composer(t: str):
    """Find '(Composer)' / '(Composer-Arranger)' / '(Nickname)' in a title.

    Returns (composer, arranger, before, nickname_text, after) or None."""
    for m in re.finditer(r"\(([^()]*)\)", t):
        inner = m[1].strip()
        comp = arr = nick = None
        if _key(inner) in _NICKNAMES:
            comp, nick = _NICKNAMES[_key(inner)]
        elif find_composer(inner):
            comp = find_composer(inner)
        elif "-" in inner:
            a, b = inner.split("-", 1)
            if find_composer(a) and find_composer(b):
                comp, arr = find_composer(a), find_composer(b)
        if comp and comp != UNKNOWN:
            return comp, arr, t[:m.start()].strip(), nick or "", t[m.end():].strip()
    return None


def _fix_words(t: str) -> str:
    for pat, rep in D.WORD_FIXES:
        t = re.sub(pat, rep, t)
    return t


def _format(core: str, opus_ok: bool):
    """Clean the name part of a title. Returns (title, movement list or None)."""
    mov = None
    t = _fix_words(core.strip(" ,-"))
    t = re.sub(r"\bNo\.\s*", "No. ", t)
    t = re.sub(r"\bNos\.\s*", "Nos. ", t)
    t = re.sub(r"\bOp\.\s*", "Op. ", t)
    t = re.sub(r"\bK\.?\s?(\d+)\b", r"K. \1", t)
    t = re.sub(r"(?<=\w)\s+(Op\.|K\.) ", r", \1 ", t)          # "Sonata Op. 47" -> "Sonata, Op. 47"
    # Nos. 5-7 / Nos. 15,12,16
    t = re.sub(r"\bNos\. (\d+)\s*-\s*(\d+)", r"Nos. \1–\2", t)
    t = re.sub(r"\bNos?\. (\d+(?:\s*,\s*\d+)+)",
               lambda m: "Nos. " + ", ".join(re.findall(r"\d+", m[1])), t)

    nick = ""
    m = re.search(r"\s+('[^']+')$", t)
    if m:
        nick, t = " " + m[1], t[:m.start()]

    key = ""
    m = re.search(r"\s+in\s+([A-Ga-g][b#]?(?:\s+(?:minor|major))?)(?=\s|,|$)", t)
    if not m:
        m = re.search(r"\s+([A-G][b#]?\s+(?:minor|major)|[A-G][b#]|[A-G]|[a-g])$", t)
    if m and not re.search(r"\bNo\.$", t[:m.start()]):
        key, t = " in " + _key_name(m[1]), (t[:m.start()] + t[m.end():]).strip()

    opus = ""
    word_is = lambda words, s: any(re.search(rf"\b{re.escape(w)}\.?$", s) for w in words)
    numbered = re.search(r"\b(No|Nos|Op|K)\. [\d,\s–]+$", t)    # already "No. 5", "Nos. 1, 3"
    m = None if numbered else re.fullmatch(r"(.*?\D)\s+(\d+)\s*-\s*(\d+(?:\s*,\s*\d+)*)", t)
    if m and not re.search(r"(No\.|Op\.|K\.)$", m[1]):
        name, n, rest = m[1], m[2], re.findall(r"\d+", m[3])
        if not opus_ok or word_is(D.NUMBER_WORDS, name):
            t, opus, mov = name, f" No. {n}", [int(x) for x in rest]
        else:
            if len(rest) == 1:
                name = D.SINGULAR.get(name, name)
            nos = f"No. {rest[0]}" if len(rest) == 1 else "Nos. " + ", ".join(rest)
            t, opus = name, f", Op. {n} {nos}"
    elif not numbered:
        m = re.fullmatch(r"(.*?\D)\s+(\d+(?:\s*,\s*\d+)+)", t)
        if m and not re.search(r"(Nos?\.|Op\.)$", m[1]):
            t, opus = m[1], " Nos. " + ", ".join(re.findall(r"\d+", m[2]))
        else:
            m = re.fullmatch(r"(.*?\D)\s+(\d+)", t)
            if m and not re.search(r"(No\.|Nos\.|Op\.|K\.)$", m[1]):
                name, n = m[1], m[2]
                if word_is(D.BARE_NUMBER_WORDS, name):
                    pass
                elif not opus_ok or word_is(D.NUMBER_WORDS, name):
                    t, opus = name, f" No. {n}"
                else:
                    t, opus = D.SINGULAR.get(name, name), f", Op. {n}"
    # "Tango in D, Op. 165 No. 2" but "Scherzo No. 1 in B minor"
    if key and opus.startswith(","):
        out = f"{t}{key}{opus}{nick}"
    elif key:
        out = f"{t}{opus}{key}{nick}"
    else:
        out = f"{t}{opus}{nick}"
    out = re.sub(r"\s+", " ", out).strip(" ,")
    out = re.sub(r",\s*,", ",", out)
    return out, mov


def parse_title(original: str, composer: str | None = None) -> dict:
    """One title -> {title, movement, performer, roll, composer, arranger, unknown_pianist}.

    `composer` is the slug the v1 entry was filed under (None/"unknown" if it wasn't)."""
    t = re.sub(r"\s+", " ", original).strip()
    t, performer, roll, stray = _strip_roll(t)

    arranger, nick, mov = None, "", None
    found = _parens_composer(t)
    if found:
        composer, arranger, before, nick, after = found
        m = re.fullmatch(r"-\s*(intro|[\d][\d,\s\-]*)\s*(.*)", after)
        if m:
            if m[1] == "intro":
                before += " (Introduction)"
            else:
                mov = _numbers(m[1])
            after = m[2]
        t = (before + " " + after).strip() if after else before
    elif not composer or composer == UNKNOWN:
        for prefix, slug in D.TITLE_COMPOSERS:
            if t.lower().startswith(prefix.lower()):
                composer = slug
                break

    if mov is None:
        m = re.fullmatch(r"(.*?)\s+[Mm]ovs?\.?\s+([\d,\s\-]+?)\s*(\(.*\))?", t)
        if m:
            t, mov = (m[1] + (" " + m[3] if m[3] else "")), _numbers(m[2])
        else:
            # "Carnaval-1", "Sonata No.11-1": a movement; "Etude 10-1" is left for _format
            m = re.fullmatch(r"(.*?)-(\d+(?:\s*,\s*\d+)*)", t)
            if m and (not re.search(r"\d$", m[1]) or re.search(r"No\.\s?\d+$", m[1])):
                t, mov = m[1], _numbers(m[2])

    info = _COMPOSERS.get(composer or UNKNOWN, {})
    title, inner_mov = _format(t, info.get("opus", True))
    if mov is None:
        mov = inner_mov
    if nick:
        title += nick
    return {"title": title, "movement": roman(mov) if mov else None, "performer": performer,
            "roll": roll, "composer": composer or UNKNOWN, "arranger": arranger,
            "unknown_pianist": stray}


# ---------------------------------------------------------------- the catalogue

def _natural(s: str):
    return [(0, int(x), "") if x.isdigit() else (1, 0, x)
            for x in re.split(r"(\d+)", ascii_fold(s).lower()) if x]


def _movement_order(m: str | None) -> int:
    if not m:
        return 0
    first = re.split(r"[–,\s]", m)[0]
    return _ROMAN.index(first) if first in _ROMAN else 99


def tidy(lib: dict, duration=None) -> dict:
    """library.json (v1 or already v2) -> v2. `duration(work_id) -> seconds | None` if given."""
    # Gather every work once, with every composer it was filed under.
    works: dict[str, dict] = {}
    filed: dict[str, list[str]] = {}
    synth: dict[str, dict] = {}
    for c in lib.get("composers", []):
        cname = c.get("name", "")
        slug = find_composer(cname)
        if slug is None:                  # not in the table: keep it as a composer of its own
            info = _synth_composer(cname)
            slug = _slugify(info["short"])
            while slug in _COMPOSERS or (slug in synth and synth[slug]["aliases"] != info["aliases"]):
                slug += "-x"
            synth[slug] = info
        for w in c.get("works", []):
            wid = w["id"]
            if wid not in works:
                works[wid] = w
            filed.setdefault(wid, []).append(slug)

    table = {**_COMPOSERS, **synth}
    by_composer: dict[str, list[dict]] = {}
    for wid, w in works.items():
        original = w.get("original") or w.get("title", "")
        candidates = filed[wid]
        known = [s for s in candidates if s != UNKNOWN]
        # filed under several composers and nothing says which: the one with most entries
        home = max(known, key=lambda s: (sum(1 for x in filed.values() if s in x), -known.index(s))) \
            if known else UNKNOWN
        over = D.WORKS.get(wid, {})
        p = parse_title(original, over.get("composer", home))
        for k in ("composer", "title", "performer", "roll", "arranger"):
            if k in over:
                p[k] = over[k]
        if "movement" in over:
            p["movement"] = over["movement"]
        if p["composer"] not in table:
            p["composer"] = UNKNOWN
        title = p["title"]
        if p["arranger"] and "arr." not in title:
            arr = p["arranger"]
            arr = table[arr]["short"] if arr in table else arr
            title += f" (arr. {arr})"
        d = (duration(wid) if duration else None) or w.get("duration")
        work = {"id": wid, "title": title, "url": w.get("url", f"/midi-files/{wid}.mid"),
                "movement": p["movement"], "performer": p["performer"], "roll": p["roll"],
                "duration": round(d, 1) if isinstance(d, (int, float)) else None,
                "original": original}
        if p["arranger"]:
            work["arranger"] = p["arranger"]
        if p["unknown_pianist"] and not p["performer"]:
            work["performer"] = _slugify(p["unknown_pianist"])
            synth_p = {"name": p["unknown_pianist"], "short": p["unknown_pianist"],
                       "born": None, "died": None}
            work["_new_pianist"] = synth_p
        by_composer.setdefault(p["composer"], []).append(work)

    composers = []
    for slug, ws in by_composer.items():
        ws.sort(key=lambda w: (_natural(re.sub(r"\s*\((arr\.|Introduction).*?\)", "", w["title"])),
                               _movement_order(w["movement"]), w["id"]))
        composers.append({**_composer_entry(slug, table[slug]), "works": ws})
    composers.sort(key=lambda c: (c["slug"] == UNKNOWN, c["sort"], c["slug"]))

    pianists: dict[str, dict] = {}
    for c in composers:
        for w in c["works"]:
            new = w.pop("_new_pianist", None)
            if not w["performer"]:
                continue
            info = D.PIANISTS.get(w["performer"]) or new or {"name": w["performer"]}
            pe = pianists.setdefault(w["performer"], {
                "name": info["name"], "slug": w["performer"], "short": info.get("short", info["name"]),
                "born": info.get("born"), "died": info.get("died"), "works": []})
            pe["works"].append(w["id"])
    plist = sorted(pianists.values(), key=lambda p: (-len(p["works"]), ascii_fold(p["short"]).lower()))
    return {"version": 2, "composers": composers, "pianists": plist}
