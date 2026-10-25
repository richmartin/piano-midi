import json
import os

import pytest

from pianod.library import Library


LIB = {"composers": [
    {"name": "Albeniz, Isaac", "slug": "albeniz-isaac", "works": [
        {"id": "file-id-000009", "title": "Aragon", "url": "/midi-files/file-id-000009.mid"},
        {"id": "file-id-000030", "title": "Cordoba", "url": "/midi-files/file-id-000030.mid"},
    ]},
    {"name": "Bach, J.S.", "slug": "bach-js", "works": [
        {"id": "file-id-000100", "title": "Prelude", "url": "/midi-files/file-id-000100.mid"},
    ]},
]}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "library.json").write_text(json.dumps(LIB))
    (tmp_path / "midi-files").mkdir()
    (tmp_path / "midi-files" / "file-id-000009.mid").write_bytes(b"MThd-ish")
    return str(tmp_path)


def test_lookups(root):
    lib = Library(root)
    assert [c["slug"] for c in lib.composers] == ["albeniz-isaac", "bach-js"]
    assert lib.work("file-id-000030")["title"] == "Cordoba"
    assert lib.work("file-id-000030")["composer"] == "Albeniz, Isaac"
    assert len(lib.works_of("albeniz-isaac")) == 2
    assert len(lib.all_works()) == 3
    with pytest.raises(ValueError):
        lib.work("file-id-999999")
    with pytest.raises(ValueError):
        lib.works_of("nobody")


def test_path_and_missing(root):
    lib = Library(root)
    assert lib.path(lib.work("file-id-000009")).endswith("midi-files/file-id-000009.mid")
    assert sorted(w["id"] for w in lib.missing()) == ["file-id-000030", "file-id-000100"]


def test_path_refuses_traversal(root):
    lib = Library(root)
    with pytest.raises(ValueError):
        lib.path({"id": "x", "url": "/midi-files/"})


def test_empty_mirror_is_fine(tmp_path):
    lib = Library(str(tmp_path))
    assert lib.composers == []
    assert lib.all_works() == []


def test_sync_fetches_whats_missing(root):
    lib = Library(root)
    fetched_urls = []

    def fetch(url):
        fetched_urls.append(url)
        if url.endswith("library.json"):
            return json.dumps(LIB).encode()
        return b"fake midi " + url.encode()

    result = lib.sync("https://server.example", fetch=fetch)
    assert result == {"works": 3, "fetched": 2}
    assert os.path.exists(os.path.join(root, "midi-files", "file-id-000030.mid"))
    # The file that was already there was not re-downloaded.
    assert not any(u.endswith("file-id-000009.mid") for u in fetched_urls)


def test_sync_rejects_a_malformed_library(root):
    lib = Library(root)
    before = lib.all_works()

    def fetch(url):
        return json.dumps({"composers": [{"name": "X"}]}).encode()     # no slug, no works

    with pytest.raises(ValueError):
        lib.sync("https://server.example", fetch=fetch)
    lib.load()
    assert len(lib.all_works()) == len(before)       # the old mirror survived


def test_sync_rejects_non_json(root):
    lib = Library(root)
    with pytest.raises(ValueError):
        lib.sync("https://server.example", fetch=lambda url: b"<html>oops</html>")


def test_recordings(root):
    lib = Library(root)
    path = lib.recording_path("My Take")
    assert path.endswith("recordings/My Take.mid")
    open(path, "wb").write(b"x")
    assert lib.recording_path("My Take").endswith("My Take-2.mid")    # never overwrites
    recs = lib.recordings()
    assert recs[0]["title"] == "My Take"
    assert lib.work(recs[0]["id"])["url"] == "/recordings/My Take.mid"
    assert lib.path(recs[0]).endswith("recordings/My Take.mid")


def test_recording_path_sanitises_titles(root):
    lib = Library(root)
    assert lib.recording_path("../../etc/passwd").endswith("recordings/etcpasswd.mid")
    assert lib.recording_path("  ").endswith("recordings/untitled.mid")
