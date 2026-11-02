"""The companion: a search in code between two calls to Zeus (faked here)."""

import json

import pytest

from pianod import companion, daemon
from pianod.library import Library
from test_daemon import d, ok  # noqa: F401 - the fixture


def stats(npm, key, pitch=60, vel=60):
    return {"npm": npm, "vel": vel, "pitch": pitch, "low": pitch - 20, "high": pitch + 20,
            "pedal": 0.5, "poly": 1.5, "key": key, "keyfit": 0.9, "notes": 100}


LIB = {"composers": [
    {"name": "Frédéric Chopin", "slug": "chopin", "works": [
        {"id": "w1", "title": "Prelude, Op. 28 No. 4", "url": "/midi-files/w1.mid", "duration": 130,
         "stats": stats(80, "E minor", pitch=52, vel=40)},
        {"id": "w2", "title": "Etude, Op. 10 No. 4", "url": "/midi-files/w2.mid", "duration": 120,
         "stats": stats(1400, "C# minor", vel=80)}]},
    {"name": "George Gershwin", "slug": "gershwin", "works": [
        {"id": "w3", "title": "Rhapsody in Blue", "url": "/midi-files/w3.mid", "duration": 750,
         "stats": stats(871, "G major", pitch=67, vel=67)}]},
    {"name": "Claude Debussy", "slug": "debussy", "works": [
        {"id": "w4", "title": "Clair de Lune", "url": "/midi-files/w4.mid", "duration": 300,
         "stats": stats(200, "Db major", pitch=70, vel=45)}]},
]}


@pytest.fixture
def lib(tmp_path):
    (tmp_path / "library.json").write_text(json.dumps(LIB))
    return Library(str(tmp_path))


class Zeus:
    """Answers the plan call, then the reply call; records the prompts."""

    def __init__(self, plan, reply):
        self.answers, self.prompts = [plan, reply], []

    def __call__(self, prompt, system, schema):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def say(text, *before):
    return [*before, {"role": "user", "text": text}]


def test_the_fastest_is_found_by_code_and_quoted(lib):
    z = Zeus({"search": True, "order": "fastest"},
             {"reply": "Chopin's Op. 10 No. 4, 1400 notes a minute!", "action": "suggest", "ids": ["w2"]})
    r = companion.turn(lib, say("What's the fastest piece?"), [], [], {}, zeus=z)
    assert r["action"] == "suggest" and [w["id"] for w in r["works"]] == ["w2"]
    shortlist = z.prompts[1].split("the ones to choose from:")[1]
    assert shortlist.index("w2 |") < shortlist.index("w3 |")          # fastest first
    assert "1400 npm" in shortlist


def test_dark_and_broody_ranks_the_minor_low_slow_piece_first(lib):
    z = Zeus({"search": True, "order": "relevance", "darkness": 0.8, "energy": -0.3},
             {"reply": "Playing the E minor prelude.", "action": "play", "ids": ["w1"]})
    r = companion.turn(lib, say("Play something dark and broody"), [], [], {}, zeus=z)
    shortlist = z.prompts[1].split("the ones to choose from:")[1].strip().splitlines()
    assert shortlist[0].startswith("w1 |")
    assert r["action"] == "play"


def test_it_cant_choose_a_work_it_wasnt_shown(lib):
    z = Zeus({"search": True, "order": "relevance", "composers": ["Debussy"]},
             {"reply": "Try this!", "action": "play", "ids": ["w3", "made-up"]})
    r = companion.turn(lib, say("Something by Debussy"), [], [], {}, zeus=z)
    assert r["action"] == "none" and r["works"] == []


def test_yes_plays_what_was_suggested_and_favourites_are_in_the_prompt(lib):
    z = Zeus({"search": False, "order": "relevance"},
             {"reply": "Great. Playing it now.", "action": "play", "ids": ["w4"]})
    convo = say("Sure, let's try that.",
                {"role": "user", "text": "I don't know what to listen to"},
                {"role": "assistant", "text": "How about Clair de Lune?", "ids": ["w4"]})
    r = companion.turn(lib, convo, ["w3"], [{"id": "w1", "title": "Prelude", "composer": "Chopin",
                                             "finished": True}], {}, zeus=z)
    assert r["action"] == "play" and r["works"][0]["id"] == "w4"
    assert "Favourites: Gershwin: Rhapsody in Blue" in z.prompts[1]
    assert "Played lately (newest first): Chopin: Prelude" in z.prompts[1]


def test_a_list_with_years(lib):
    z = Zeus({"search": True, "order": "relevance", "composed_after": 1920, "wants_list": True},
             {"reply": "Yes:", "action": "list", "ids": ["w3"], "years": [1924]})
    r = companion.turn(lib, say("Anything composed after 1920?"), [], [], {}, zeus=z)
    assert r["works"] == [{"id": "w3", "title": "Rhapsody in Blue", "movement": None,
                           "composer": "Gershwin", "duration": 750, "year": 1924}]
    assert "w1 |" not in z.prompts[1]                  # Chopin died in 1849


def test_through_the_daemon(d, monkeypatch):  # noqa: F811
    assert ok(d, "companion", probe=1)["ready"] is False
    r = d.handle({"cmd": "companion", "messages": say("hi")})
    assert not r["ok"] and "set up" in r["error"]
    monkeypatch.setattr(companion, "ready", lambda: True)
    monkeypatch.setattr(companion, "_zeus", Zeus({"search": True, "order": "relevance"},
                                                 {"reply": "Here's Bach.", "action": "play",
                                                  "ids": ["file-id-000001"]}))
    monkeypatch.setattr(companion.turn, "__defaults__", (companion._zeus,))
    r = ok(d, "companion", messages=say("play anything"))
    assert r["action"] == "play" and d.state()["title"] == "Prelude"
