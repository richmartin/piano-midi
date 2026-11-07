import json

import pytest

from pianod import pipeline
from pianod.scores import PipelineError


class FakeSaxy:
    """Saxy's scores API as saxy2 described it (2026-11-07), scripted per test."""

    def __init__(self, states, parts=None, quality=None):
        self.states = list(states)
        self.parts = parts or [{"id": "P1", "staves": 2}]
        self.quality = quality or {}
        self.calls = []

    def __call__(self, method, path, data=None, ctype="application/json"):
        self.calls.append((method, path, data, ctype))
        if method == "POST" and path == "/api/scores":
            return 202, json.dumps({"id": "s1", "state": "reading"}).encode()
        if method == "POST" and path.endswith("/parts"):
            self.states.insert(0, "performing")
            return 202, b"{}"
        if path.endswith("/midi"):
            return 200, b"MThd performance"
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return 200, json.dumps({"state": state, "parts": self.parts, "quality": self.quality,
                                "warnings": ["tempo guessed"], "error": "unreadable" if state == "failed" else None}).encode()


@pytest.fixture
def score(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SAXY_URL", "http://saxy")
    monkeypatch.setattr(pipeline, "SAXY_TOKEN", "t")
    p = tmp_path / "input.pdf"
    p.write_bytes(b"%PDF-1.4 scan")
    return str(p), {"title": "Gymnopédie No. 1", "composer": "Erik Satie", "source": "upload"}


def run(monkeypatch, fake, path, rec, kind="pdf"):
    monkeypatch.setattr(pipeline, "_request", fake)
    return pipeline.convert(path, kind, rec, sleep=lambda s: None, clock=iter(range(10 ** 6)).__next__)


def test_a_pdf_is_sent_read_and_its_performance_returned(monkeypatch, score):
    fake = FakeSaxy(["reading", "performing", "ready"])
    path, rec = score
    assert run(monkeypatch, fake, path, rec) == b"MThd performance"
    method, url, body, ctype = fake.calls[0]
    assert (method, url) == ("POST", "/api/scores") and ctype.startswith("multipart/form-data")
    assert b"%PDF-1.4 scan" in body and b'"composer": "Erik Satie"' in body
    assert rec["pipeline_id"] == "s1" and rec["warnings"] == ["tempo guessed"]


def test_one_two_staff_part_is_chosen_for_rich(monkeypatch, score):
    fake = FakeSaxy(["review", "ready"], parts=[{"id": "P1", "staves": 1}, {"id": "P2", "staves": 2}])
    path, rec = score
    run(monkeypatch, fake, path, rec)
    [post] = [c for c in fake.calls if c[1].endswith("/parts")]
    assert json.loads(post[2]) == {"melody": "", "piano": ["P2"]}


def test_an_unclear_choice_sends_rich_to_the_review_page(monkeypatch, score):
    parts = [{"id": f"P{i}", "staves": 1} for i in range(4)]
    path, rec = score
    with pytest.raises(PipelineError, match=r"saxy2\.fam-mart\.in/#/scores/s1\?review=1"):
        run(monkeypatch, FakeSaxy(["review"], parts=parts), path, rec)
    assert rec["pipeline_id"] == "s1"            # a retry continues this score


def test_a_retry_continues_saxys_score_instead_of_sending_it_again(monkeypatch, score):
    fake = FakeSaxy(["ready"])
    path, rec = score
    rec["pipeline_id"] = "s1"
    run(monkeypatch, fake, path, rec)
    assert not [c for c in fake.calls if c[1] == "/api/scores"]


def test_failure_and_doubt_are_reported(monkeypatch, score):
    path, rec = score
    with pytest.raises(PipelineError, match="unreadable"):
        run(monkeypatch, FakeSaxy(["failed"]), path, dict(rec))
    r = dict(rec)
    run(monkeypatch, FakeSaxy(["ready"], quality={"measures": 40, "low": 8}), path, r)
    assert "doubtful" in r["warning"]


def test_not_connected_or_unreadable_kind(monkeypatch, score):
    path, rec = score
    with pytest.raises(PipelineError, match=r"\.ly"):
        run(monkeypatch, FakeSaxy(["ready"]), path, rec, kind="ly")
    monkeypatch.setattr(pipeline, "SAXY_TOKEN", "")
    with pytest.raises(PipelineError, match="isn't connected"):
        pipeline.convert(path, "pdf", rec)


def test_choose_parts():
    assert pipeline.choose_parts({"parts": [{"id": "P1", "staves": 2}, {"id": "P2", "staves": 2}]}) == ["P1", "P2"]
    assert pipeline.choose_parts({"parts": [{"id": "V", "staves": 1}], "pianoParts": ["V"]}) == ["V"]
    assert pipeline.choose_parts({"parts": [{"id": "A", "staves": 1}, {"id": "B", "staves": 1}]}) is None
