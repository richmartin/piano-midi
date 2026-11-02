"""The companion: talk to the piano about what to play.

Every model call goes through Zeus (the fleet's AI gateway: POST $ZEUS_URL/ai/v1/chat, a bearer
token per app, the model Zeus's choice). Zeus's chat has no tool use, so a turn is two calls with
the searching done here, in code, between them:

  1. plan:   the conversation -> a search (composers, era, key, dates, length, an energy and a
             darkness to aim for, an order) or none
  2. reply:  the conversation + a shortlist of at most SHORTLIST works found by that search (with
             their measurements: features.py), the favourites and what was played lately ->
             {reply, action, ids}; the ids must come from what it was shown

Then pianod does the action (play). The browser keeps the conversation and sends it each turn,
so pianod holds nothing between turns. The token lives in pianod's environment, never in the
browser and never in this repo.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import urllib.error
import urllib.request

from .catalogue import ascii_fold

LOG = logging.getLogger("pianod")

ZEUS_URL = os.environ.get("ZEUS_URL", "")
ZEUS_TOKEN = os.environ.get("ZEUS_TOKEN", "")
TIMEOUT = 90.0
TURNS = 10                  # conversation turns sent back to the model
SHORTLIST = 12
LIST_MAX = 30               # when the user asks for a list
RECENT = 15                 # recently heard: left out of suggestions unless asked for

ORDERS = ["relevance", "fastest", "slowest", "loudest", "quietest", "lowest", "highest", "longest",
          "shortest", "oldest", "newest", "random"]

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "search": {"type": "boolean", "description": "false when no new works are needed (chit-chat, "
                   "or acting on works already mentioned)"},
        "composers": {"type": "array", "items": {"type": "string"}},
        "eras": {"type": "array", "items": {"type": "string", "enum": ["baroque", "classical", "romantic", "modern"]}},
        "title_words": {"type": "array", "items": {"type": "string"},
                        "description": "words that must be in the title, e.g. nocturne, waltz, sonata"},
        "mode": {"type": ["string", "null"], "enum": ["major", "minor", None]},
        "composed_after": {"type": ["integer", "null"]},
        "composed_before": {"type": ["integer", "null"]},
        "min_minutes": {"type": ["number", "null"]},
        "max_minutes": {"type": ["number", "null"]},
        "energy": {"type": ["number", "null"], "description": "-1 calm .. 1 driving; null if it doesn't matter"},
        "darkness": {"type": ["number", "null"], "description": "-1 bright, sunny .. 1 dark, brooding; null if it doesn't matter"},
        "favourites_only": {"type": "boolean"},
        "include_recent": {"type": "boolean", "description": "true if recently played works may be suggested"},
        "order": {"type": "string", "enum": ORDERS},
        "wants_list": {"type": "boolean", "description": "the user asked for a list or how many"},
    },
    "required": ["search", "order"],
}

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "action": {"type": "string", "enum": ["none", "suggest", "play", "list"]},
        "ids": {"type": "array", "items": {"type": "string"},
                "description": "work ids, only ones you were shown: the suggestion, what to play, "
                               "or the list in order"},
        "years": {"type": "array", "items": {"type": ["integer", "null"]},
                  "description": "for a list: the year each work was composed, in the same order as ids, null if unsure"},
    },
    "required": ["reply", "action", "ids"],
}

PLAN_SYSTEM = """You turn a listener's request into a search of a player piano's MIDI library \
(about 800 classical and early-20th-century piano works: Bach to Gershwin, many piano rolls by \
great pianists). Read the whole conversation: "that one" or "yes, play it" refers to works \
already mentioned, and needs no search. For a vague request ("I don't know what to listen to"), \
search with order "relevance" and leave filters empty: the favourites and history are \
considered separately. Moods become energy and darkness: dark and broody is darkness 0.8, \
energy -0.3; joyful is darkness -0.7, energy 0.5. "Composed after 1950" is composed_after 1950. \
Answer with the JSON object only."""

REPLY_SYSTEM = """You are the companion of a 1980s Yamaha Disklavier grand piano in Rich's \
house: it plays MIDI files by itself. You talk with the listener about what to play, warmly and \
briefly (one to three sentences, no lists in the prose), like a well-read friend who knows the \
music. You only ever choose works from those you are shown, by their id.

Actions: "suggest" offers works and waits for a yes; "play" plays them now (when the listener \
asked you to play something, or agreed to a suggestion); "list" answers a question with a list \
(give ids, and years when asked about dates); "none" is conversation only. When you suggest, \
say why in a phrase that connects it to what they like (their favourites, what they played \
lately) or to what they asked for. Use the measurements when they help: npm is notes per \
minute, so "the fastest" is the highest npm, and you can quote it. When you list works with \
years, use what you know of when each was composed and leave out works you believe don't \
match. If nothing shown fits, say so honestly. Answer with the JSON object only."""


class CompanionError(Exception):
    pass


def ready() -> bool:
    return bool(ZEUS_URL and ZEUS_TOKEN)


def _zeus(prompt: str, system: str, schema: dict) -> dict:
    body = json.dumps({"prompt": prompt, "system": system, "json": True, "schema": schema,
                       "temperature": 0.4}).encode()
    req = urllib.request.Request(ZEUS_URL.rstrip("/") + "/ai/v1/chat", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {ZEUS_TOKEN}",
                                          "X-Zeus-Priority": "interactive"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        LOG.warning("companion: zeus %s: %s", e.code, e.read()[:300])
        raise CompanionError(f"the AI service said {e.code}") from e
    except (OSError, ValueError) as e:
        LOG.warning("companion: zeus: %s", e)
        raise CompanionError("the AI service didn't answer") from e
    text = out.get("text") or ""
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)          # a model that wrapped its JSON in prose
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    LOG.warning("companion: not JSON from %s: %s", out.get("model"), text[:300])
    raise CompanionError("the AI's answer didn't make sense")


# --- the library, as the companion sees it ----------------------------------------------------
def _fold(s) -> str:
    return ascii_fold(str(s or "")).lower()


class Catalogue:
    """The works with their composers' dates and the measurements scaled to compare."""

    def __init__(self, lib):
        comps = {c.get("slug"): c for c in lib.composers}
        self.works = []
        for w in lib.all_works():
            c = comps.get(w.get("composer_slug")) or {}
            self.works.append({**w, "born": c.get("born"), "died": c.get("died"), "era": c.get("era")})
        self.by_id = {w["id"]: w for w in self.works}
        # z-scores of the measurements, for moods and orders
        self.z: dict[str, dict] = {}
        for k in ("npm", "vel", "pitch", "poly"):
            xs = [w["stats"][k] for w in self.works if w.get("stats") and w["stats"].get(k) is not None]
            if len(xs) < 2:
                continue
            m = sum(xs) / len(xs)
            sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 or 1.0
            for w in self.works:
                v = (w.get("stats") or {}).get(k)
                if v is not None:
                    self.z.setdefault(w["id"], {})[k] = (v - m) / sd

    def energy(self, w) -> float | None:
        z = self.z.get(w["id"])
        if not z or "npm" not in z:
            return None
        return 0.6 * z["npm"] + 0.25 * z.get("vel", 0) + 0.15 * z.get("poly", 0)

    def darkness(self, w) -> float | None:
        s, z = w.get("stats"), self.z.get(w["id"])
        if not s or not z:
            return None
        minor = (1 if s.get("key", "").endswith("minor") else -1) * (s.get("keyfit") or 0.5)
        return 0.9 * minor - 0.4 * z.get("pitch", 0) - 0.2 * z.get("npm", 0)

    def search(self, q: dict, favourites: list[str], recent: list[str]) -> tuple[list[dict], int]:
        ws = self.works
        if q.get("favourites_only"):
            ws = [w for w in ws if w["id"] in favourites]
        names = [_fold(n) for n in q.get("composers") or [] if n]
        if names:
            ws = [w for w in ws if any(n in _fold(w.get("composer")) or n in _fold(w.get("composer_short"))
                                       for n in names)]
        if q.get("eras"):
            ws = [w for w in ws if w.get("era") in q["eras"]]
        for word in q.get("title_words") or []:
            ws = [w for w in ws if _fold(word) in _fold(w["title"])]
        if q.get("mode"):
            ws = [w for w in ws if (w.get("stats") or {}).get("key", "").endswith(q["mode"])]
        after, before = q.get("composed_after"), q.get("composed_before")
        if after:      # composers alive after then, or of unknown dates (the model judges the year)
            ws = [w for w in ws if (w["died"] or 9999) > after
                  and (w["died"] or w["born"] is None or w["born"] >= after - 80)]
        if before:
            ws = [w for w in ws if (w["born"] or 0) + 10 < before]
        lo, hi = q.get("min_minutes"), q.get("max_minutes")
        if lo:
            ws = [w for w in ws if (w.get("duration") or 0) >= lo * 60]
        if hi:
            ws = [w for w in ws if 0 < (w.get("duration") or 0) <= hi * 60]
        if not q.get("include_recent"):
            fresh = [w for w in ws if w["id"] not in recent[:RECENT]]
            ws = fresh or ws
        total = len(ws)

        order = q.get("order") or "relevance"
        e_t, d_t = q.get("energy"), q.get("darkness")

        def mood_fit(w):
            fit, parts = 0.0, 0
            for target, f in ((e_t, self.energy), (d_t, self.darkness)):
                if target is None:
                    continue
                v = f(w)
                if v is None:
                    return -9.0
                fit -= (v - 1.5 * target) ** 2
                parts += 1
            return fit if parts else 0.0

        stat = lambda k, d=0: lambda w: (w.get("stats") or {}).get(k, d)   # noqa: E731
        keyfns = {"fastest": (stat("npm"), True), "slowest": (stat("npm", 1e9), False),
                  "loudest": (stat("vel"), True), "quietest": (stat("vel", 999), False),
                  "lowest": (stat("pitch", 999), False), "highest": (stat("pitch"), True),
                  "longest": (lambda w: w.get("duration") or 0, True),
                  "shortest": (lambda w: w.get("duration") or 1e9, False),
                  "oldest": (lambda w: w.get("born") or 9999, False),
                  "newest": (lambda w: w.get("born") or 0, True)}
        if order == "relevance" and after:
            order = "newest"
        if order in keyfns:
            fn, rev = keyfns[order]
            ws = sorted(ws, key=fn, reverse=rev)
        elif e_t is not None or d_t is not None:
            ws = sorted(ws, key=lambda w: mood_fit(w) + random.uniform(0, 0.3), reverse=True)
        else:
            ws = random.sample(ws, len(ws))
        return ws, total


def _work_line(w: dict, year=None) -> str:
    s = w.get("stats") or {}
    bits = [w["id"], f'{w.get("composer")}', w["title"] + (f' ({w["movement"]})' if w.get("movement") else "")]
    if w.get("born") or w.get("died"):
        bits.append(f'composer {w.get("born") or "?"}-{w.get("died") or ""}')
    if w.get("performer_name"):
        bits.append(f'roll played by {w["performer_name"]}')
    if w.get("duration"):
        bits.append(f'{w["duration"] / 60:.0f} min')
    if s:
        bits.append(f'{s.get("key")}, {s.get("npm")} npm, velocity {s.get("vel")}, '
                    f'pitch {s.get("low")}-{s.get("high")} (median {s.get("pitch")})')
    return " | ".join(str(b) for b in bits)


def _conversation(messages: list[dict], cat: Catalogue) -> str:
    out = []
    for m in messages[-TURNS:]:
        who = "Listener" if m.get("role") == "user" else "Companion"
        line = f'{who}: {str(m.get("text", ""))[:1000]}'
        named = [cat.by_id[i] for i in m.get("ids") or [] if i in cat.by_id]
        if named:
            line += "  [works mentioned: " + "; ".join(f'{w["id"]} {w["composer_short"]}: {w["title"]}'
                                                       for w in named[:LIST_MAX]) + "]"
        out.append(line)
    return "\n".join(out)


def turn(lib, messages: list[dict], favourites: list[str], history: list[dict], now: dict,
         zeus=_zeus) -> dict:
    """One exchange. messages: [{role: user|assistant, text, ids?}], the last the listener's.
    -> {reply, action, works: [{id, title, composer, year?}]}"""
    if not messages or messages[-1].get("role") != "user" or not str(messages[-1].get("text", "")).strip():
        raise ValueError("say something")
    cat = Catalogue(lib)
    convo = _conversation(messages, cat)
    recent = [h["id"] for h in history]

    plan = zeus(f"Conversation so far:\n{convo}\n\nThe search for the listener's last message:",
                PLAN_SYSTEM, PLAN_SCHEMA)
    LOG.info("companion plan: %s", json.dumps(plan)[:400])
    shown: list[dict] = []
    total = None
    if plan.get("search", True):
        found, total = cat.search(plan, favourites, recent)
        shown = found[:LIST_MAX if plan.get("wants_list") or plan.get("composed_after") else SHORTLIST]
    # works already in the conversation, so "yes, play that" can be answered
    for m in messages[-4:]:
        for i in m.get("ids") or []:
            if i in cat.by_id and cat.by_id[i] not in shown:
                shown.append(cat.by_id[i])

    favs = [cat.by_id[i] for i in favourites if i in cat.by_id]
    heard = [h for h in history if h.get("id") in cat.by_id][:12]
    parts = [f"The library has {len(cat.works)} works by {len({w.get('composer_slug') for w in cat.works})} composers."]
    if now.get("playing") and now.get("title"):
        parts.append(f'Playing now: {now.get("composer")}: {now["title"]}.')
    parts.append("Favourites: " + ("; ".join(f'{w["composer_short"]}: {w["title"]}' for w in favs[:25]) or "none yet"))
    parts.append("Played lately (newest first): " + ("; ".join(
        f'{h.get("composer")}: {h.get("title")}' + ("" if h.get("finished") else " (not finished)")
        for h in heard) or "nothing yet"))
    if total is not None:
        parts.append(f"The search for this request matched {total} works; the ones to choose from:")
    else:
        parts.append("Works to choose from:")
    parts.append("\n".join(_work_line(w) for w in shown) or "(none)")
    parts.append(f"\nConversation:\n{convo}\n\nYour answer to the listener's last message:")
    ans = zeus("\n".join(parts), REPLY_SYSTEM, REPLY_SCHEMA)

    allowed = {w["id"] for w in shown}
    ids = [i for i in ans.get("ids") or [] if i in allowed]
    years = ans.get("years") or []
    action = ans.get("action") if ans.get("action") in ("none", "suggest", "play", "list") else "none"
    if action in ("play", "suggest") and not ids:
        action = "none"
    works = []
    for n, i in enumerate(ids[:LIST_MAX]):
        w = cat.by_id[i]
        item = {"id": i, "title": w["title"], "movement": w.get("movement"),
                "composer": w.get("composer_short") or w.get("composer"), "duration": w.get("duration")}
        if action == "list" and n < len(years) and isinstance(years[n], int):
            item["year"] = years[n]
        works.append(item)
    reply = str(ans.get("reply") or "").strip() or "Sorry, I lost my thread there."
    return {"reply": reply, "action": action, "works": works}
