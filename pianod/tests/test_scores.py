import json

import mido
import pytest

from pianod import scores, sources
from pianod.library import Library


def midi_bytes(notes=((60, 0.5), (64, 0.5))) -> bytes:
    f = mido.MidiFile()
    t = mido.MidiTrack()
    f.tracks.append(t)
    for n, dur in notes:
        t.append(mido.Message("note_on", note=n, velocity=60, time=0))
        t.append(mido.Message("note_off", note=n, velocity=0, time=int(dur * 960)))
    import io
    b = io.BytesIO()
    f.save(file=b)
    return b.getvalue()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "library.json").write_text(json.dumps({"composers": [
        {"name": "Chopin, Frederic", "works": [
            {"id": "file-id-000001", "title": "Nocturne Op.9 No.2", "url": "/midi-files/file-id-000001.mid"}]}]}))
    return str(tmp_path)


def test_a_fetched_score_converts_and_joins_its_composer(root):
    seen = {}

    def convert(path, kind, rec):
        seen["kind"] = kind
        assert open(path, "rb").read() == b"MThd mechanical"
        return midi_bytes()

    lib = None
    s = scores.Scores(root, convert, on_ready=lambda: lib.load(),
                      fetch=lambda url: b"MThd mechanical", accepts=("xml", "mid", "pdf"))
    lib = Library(root, scores=s.ready)
    item = {"source": "mutopia", "ref": "1590", "title": "Nocturne Op. 9 No. 3", "composer": "F. F. Chopin",
            "formats": {"pdf": "https://x/a.pdf", "mid": "https://x/a.mid"}, "page": "https://x"}
    rec = s.add_item(item)
    assert rec["state"] == "queued" and rec["input"] == "mid"        # mid before pdf: what it takes best
    assert s.step() and not s.step()
    assert seen["kind"] == "mid"
    [r] = s.list()
    assert r["state"] == "ready" and r["duration"] == 1.0
    w = lib.work(r["id"])
    assert w["score"] and w["source"] == "mutopia"
    assert w["composer"] == lib.work("file-id-000001")["composer"]   # filed under the same Chopin
    assert lib.path(w).endswith(f"scores/{r['id']}.mid")
    assert r["id"] not in {m["id"] for m in lib.missing()}            # ours, never fetched from murdoch


def test_a_failed_conversion_is_kept_with_its_reason_and_can_retry(root):
    calls = []

    def convert(path, kind, rec):
        calls.append(1)
        if len(calls) == 1:
            raise scores.PipelineError("the reading was doubtful")
        return midi_bytes()

    s = scores.Scores(root, convert)
    rec = s.add_upload("Gymnopedie_1.pdf", b"%PDF-1.4 ...", {"composer": "Erik Satie"})
    assert rec["title"] == "Gymnopedie 1"
    s.step()
    [r] = s.list()
    assert r["state"] == "failed" and "doubtful" in r["error"]
    s.retry(r["id"])
    s.step()
    assert s.list()[0]["state"] == "ready"


def test_not_a_midi_from_the_pipeline_fails_the_score(root):
    s = scores.Scores(root, lambda *a: b"<html>oops")
    s.add_upload("x.pdf", b"%PDF", {})
    s.step()
    assert "didn't return a MIDI" in s.list()[0]["error"]


def test_a_handoff_is_ready_at_once_and_a_reperform_replaces_it(root):
    s = scores.Scores(root, convert=None)
    meta = {"title": "Ständchen", "composer": "Franz Schubert",
            "source": {"kind": "saxy", "ref": "saxy-42", "url": "https://saxy/42"}}
    a = s.add_ready(meta, midi_bytes())
    b = s.add_ready(meta, midi_bytes(((60, 1.0), (62, 1.0))))
    assert a["id"] == b["id"] and len(s.list()) == 1
    assert s.list()[0]["duration"] == 2.0
    with pytest.raises(ValueError):
        s.add_ready(meta, b"not midi")


def test_restart_requeues_what_was_converting(root, tmp_path):
    s = scores.Scores(root, convert=None)
    rec = s.add_upload("a.mid", b"MThd", {})
    s.items[rec["id"]]["state"] = "converting"
    s._save()
    assert scores.Scores(root, convert=None).list()[0]["state"] == "queued"


def test_only_what_the_pipeline_takes_is_fetched(root):
    s = scores.Scores(root, convert=None, accepts=("xml",), fetch=lambda u: b"x")
    with pytest.raises(ValueError, match="download it"):
        s.add_item({"title": "t", "formats": {"pdf": "https://x/a.pdf"}})
    with pytest.raises(ValueError):
        s.add_upload("evil.exe", b"MZ", {})


MUTOPIA_PAGE = """<table class="table-bordered result-table"> <tr><td>Nocturne</td> <td>by F. F. Chopin (1810–1849)</td>
<td>Op. 9, No. 2</td> <td>&nbsp;</td> </tr><tr> <td>for Piano</td> <td>1833</td> <td>Romantic</td> <td></td> </tr><tr>
<td>G. Schirmer, New York, 1881</td> <td><a href="../legal.html#ccasa">Creative Commons Attribution-ShareAlike 3.0</a></td>
<td><a href="piece-info.cgi?id=1590">More Information</a></td> <td>2014/12/18</td> </tr><tr>
<td>Download: <a href="https://www.mutopiaproject.org/ftp/C/n2/n2.ly">.ly file</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/C/n2/n2.mid">.mid file</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/C/n2/n2-a4.pdf">A4 .pdf</a></td></tr></table>"""


def test_mutopia_results_parse():
    [r] = sources.parse_mutopia(MUTOPIA_PAGE)
    assert r["composer"] == "F. F. Chopin" and r["composer_dates"] == "1810–1849"
    assert r["detail"].startswith("Op. 9, No. 2") and r["auto"]
    assert set(r["formats"]) == {"ly", "mid", "pdf"} and r["formats"]["pdf"].endswith("-a4.pdf")
    assert r["page"].endswith("id=1590")


def test_imslp_results_link_out_and_a_dead_source_doesnt_empty_the_list():
    data = {"query": {"search": [{"title": "Gymnopédies (Satie, Erik)"}, {"title": "Category:Satie"}]}}

    def get(url):
        if "imslp" in url:
            return json.dumps(data).encode()
        raise OSError("mutopia is down")

    [r] = sources.search("satie", get=get)
    assert r["source"] == "imslp" and r["composer"] == "Erik Satie" and not r["auto"]
    assert r["page"] == "https://imslp.org/wiki/Gymnop%C3%A9dies_%28Satie%2C_Erik%29"


def test_openscore_songs_are_found_by_composer_or_title_without_accents():
    tree = {"tree": [
        {"path": "scores/Schubert,_Franz/Schwanengesang,_D.957/04_Ständchen/lc5148063.mxl"},
        {"path": "scores/Schubert,_Franz/Schwanengesang,_D.957/04_Ständchen/lc5148063.mscx"},
        {"path": "scores/Hensel,_Fanny/_/Schwanenlied/lc123.mxl"},
        {"path": "README.md"}]}
    sources._tree.update(at=0.0, songs=[])
    get = lambda url: json.dumps(tree).encode()
    [r] = sources.openscore("schubert standchen", get=get, now=lambda: 1e9)
    assert r["composer"] == "Franz Schubert" and r["title"] == "04 Ständchen"
    assert r["detail"] == "Schwanengesang, D.957" and r["auto"] and "_text" not in r
    assert r["formats"]["mxl"].endswith("04_Ständchen/lc5148063.mxl")
    [h] = sources.openscore("schwanenlied", get=lambda u: 1 / 0, now=lambda: 1e9 + 60)   # the list is kept
    assert h["detail"] == "" and h["composer"] == "Fanny Hensel"
