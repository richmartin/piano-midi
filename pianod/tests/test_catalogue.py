import copy

import pytest

from pianod import catalogue, catalogue_data


def w(n, title):
    wid = f"file-id-{n:06d}"
    return {"id": wid, "title": title, "url": f"/midi-files/{wid}.mid"}


LIB = {"composers": [
    {"name": "Albeniz, Isaac", "slug": "albeniz-isaac", "works": [w(9, "Aragon"), w(30, "Cordoba 232-4")]},
    {"name": "Arthur Rubinstein", "slug": "arthur-rubinstein", "works": [
        w(531, "Melody in F"), w(507, "Kamennoi-Ostrow No.22"), w(507 + 1, "Kamennoi-Ostrow No.15")]},
    {"name": "Bach, J.S.", "slug": "bach-js", "works": [w(274, "Prelude & Fugue 5")]},
    {"name": "Ferruccio Busoni", "slug": "ferruccio-busoni", "works": [w(274, "Prelude & Fugue 5")]},
    {"name": "Frédéric Chopin", "slug": "frederic-chopin", "works": [
        w(535, "Nocturne 15-2"), w(474, "Etude 25-9"), w(534, "Nocturne 15-1"), w(566, "Scherzo No.1 B minor")]},
    {"name": "Mndlsn", "slug": "mndlsn", "works": [w(577, "Song WoW 102-4")]},
    {"name": "Unknown Composer", "slug": "unknown-composer", "works": [
        w(356, "Sonata No.15 (Mozart)-1, Landowska DA"),
        w(358, "Sonata No.15 (Mozart)-3, Landowska DA"),
        w(357, "Sonata No.15 (Mozart)-2, Landowska DA"),
        w(330, "Sonata 14-2 (Bthvn)-2, Murdoch DA"),
        w(301, "Sonata (Pastoral)-2, 3 Friedman DA"),
        w(266, "Organ Concerto (Bach, W.F)-2 Carreras DA"),
        w(296, "Sonata (Appsnata)-1,2, Levitzki AB"),
        w(370, "Symphony 40 Mov 1 (Mozart) A"),
        w(212, "Capriccio 76-2 (Brahms), Mwitsch AA"),
        w(237, "Fur Elise, Reisenauer LW"),
        w(999, "Something nobody can place"),
    ]},
]}


@pytest.fixture
def v2():
    return catalogue.tidy(copy.deepcopy(LIB))


def comp(v2, slug):
    return next(c for c in v2["composers"] if c["slug"] == slug)


def work(v2, n):
    wid = f"file-id-{n:06d}"
    for c in v2["composers"]:
        for x in c["works"]:
            if x["id"] == wid:
                return c, x
    raise KeyError(wid)


def test_mozart_landowska_roll(v2):
    c, x = work(v2, 356)
    assert c["slug"] == "mozart" and c["name"] == "Wolfgang Amadeus Mozart"
    assert c["first"] == "Wolfgang Amadeus" and c["era"] == "classical"
    assert (c["born"], c["died"]) == (1756, 1791)
    assert x["title"] == "Sonata No. 15"
    assert x["movement"] == "I"
    assert x["performer"] == "landowska"
    assert x["roll"] == "Duo-Art"
    assert x["original"] == "Sonata No.15 (Mozart)-1, Landowska DA"
    assert x["url"] == "/midi-files/file-id-000356.mid"
    # movements in order
    assert [y["movement"] for y in c["works"] if y["title"] == "Sonata No. 15"] == ["I", "II", "III"]
    p = next(p for p in v2["pianists"] if p["slug"] == "landowska")
    assert p["name"] == "Wanda Landowska" and p["short"] == "Landowska"
    assert (p["born"], p["died"]) == (1879, 1959)
    assert set(p["works"]) == {"file-id-000356", "file-id-000357", "file-id-000358"}


def test_vowel_stripped_composers(v2):
    c, x = work(v2, 330)
    assert c["slug"] == "beethoven" and c["name"] == "Ludwig van Beethoven"
    assert x["title"] == "Sonata, Op. 14 No. 2" and x["movement"] == "II"
    assert x["performer"] == "murdoch"
    c, x = work(v2, 577)
    assert c["slug"] == "mendelssohn" and x["title"] == "Song Without Words, Op. 102 No. 4"
    c, x = work(v2, 296)
    assert c["slug"] == "beethoven" and x["title"] == "Sonata, Op. 57 'Appassionata'"
    assert x["movement"] == "I–II" and x["roll"] == "Ampico B" and x["performer"] == "levitzki"
    _, x = work(v2, 212)
    assert x["performer"] == "moiseiwitsch" and x["title"] == "Capriccio, Op. 76 No. 2"


def test_awkward_roll_titles(v2):
    c, x = work(v2, 301)                      # "-2, 3 Friedman": the 3 is a movement
    assert c["slug"] == "beethoven" and x["movement"] == "II–III" and x["performer"] == "friedman"
    assert x["title"] == "Sonata, Op. 28 'Pastoral'"
    c, x = work(v2, 266)                      # "(Bach, W.F)-2 Carreras"
    assert c["slug"] == "bach-wf" and x["title"] == "Organ Concerto"
    assert x["movement"] == "II" and x["performer"] == "carreras"
    c, x = work(v2, 370)                      # bare roll code, no pianist
    assert c["slug"] == "mozart" and x["title"] == "Symphony No. 40" and x["movement"] == "I"
    assert x["performer"] is None and x["roll"] == "Ampico"
    c, x = work(v2, 237)                      # composer known only from the title
    assert c["slug"] == "beethoven" and x["title"] == "Für Elise" and x["performer"] == "reisenauer"


def test_arthur_is_anton_rubinstein(v2):
    c = comp(v2, "rubinstein")
    assert c["name"] == "Anton Rubinstein" and (c["born"], c["died"]) == (1829, 1894)
    assert "Melody in F" in [x["title"] for x in c["works"]]
    assert not any("Arthur" in c["name"] for c in v2["composers"])


def test_albeniz_name_and_sort(v2):
    c = comp(v2, "albeniz")
    assert c["name"] == "Isaac Albéniz" and c["short"] == "Albéniz" and c["first"] == "Isaac"
    assert c["sort"] == "albeniz isaac"
    assert v2["composers"][0]["slug"] == "albeniz"
    sorts = [c["sort"] for c in v2["composers"] if c["slug"] != "unknown"]
    assert sorts == sorted(sorts)
    assert v2["composers"][-1]["slug"] == "unknown"
    assert [x["title"] for x in c["works"]] == ["Aragón", "Córdoba, Op. 232 No. 4"]


def test_opus_titles(v2):
    titles = [x["title"] for x in comp(v2, "chopin")["works"]]
    assert titles == ["Étude, Op. 25 No. 9", "Nocturne, Op. 15 No. 1", "Nocturne, Op. 15 No. 2",
                      "Scherzo No. 1 in B minor"]


def test_double_filed_transcription_goes_to_composer(v2):
    c, x = work(v2, 274)
    assert c["slug"] == "bach-js" and x["arranger"] == "busoni"
    assert x["title"] == "Prelude & Fugue No. 5 (arr. Busoni)"


def test_unknown_stays_unknown(v2):
    c, x = work(v2, 999)
    assert c["slug"] == "unknown" and c["name"] == "Unknown composer" and c["era"] is None
    assert x["title"] == "Something nobody can place"


def test_every_id_exactly_once(v2):
    before = {x["id"] for c in LIB["composers"] for x in c["works"]}
    after = [x["id"] for c in v2["composers"] for x in c["works"]]
    assert len(after) == len(set(after)) and set(after) == before


def test_keeps_v1_fields(v2):
    assert v2["version"] == 2
    for c in v2["composers"]:
        assert {"name", "slug", "works"} <= c.keys()
        for x in c["works"]:
            assert {"id", "title", "url"} <= x.keys()


def test_idempotent_on_v2(v2):
    assert catalogue.tidy(copy.deepcopy(v2)) == v2


def test_duration_callback_used_and_kept():
    asked = []

    def duration(wid):
        asked.append(wid)
        return 302.44 if wid == "file-id-000356" else None

    v2 = catalogue.tidy(copy.deepcopy(LIB), duration)
    ids = {x["id"] for c in LIB["composers"] for x in c["works"]}
    assert set(asked) == ids
    assert work(v2, 356)[1]["duration"] == 302.4
    assert work(v2, 357)[1]["duration"] is None
    again = catalogue.tidy(copy.deepcopy(v2))         # no callback: v2 durations survive
    assert work(again, 356)[1]["duration"] == 302.4


def test_pianists_most_works_first(v2):
    counts = [len(p["works"]) for p in v2["pianists"]]
    assert counts == sorted(counts, reverse=True)
    assert v2["pianists"][0]["slug"] == "landowska"


@pytest.mark.parametrize("raw,slug", [
    ("Schbrt", "schubert"), ("Franz Schubert", "schubert"), ("Schmnn", "schumann"), ("Schmn", "schumann"),
    ("Saens", "saint-saens"), ("St", "saint-saens"), ("Tchvsky", "tchaikovsky"), ("Mzkwski", "moszkowski"),
    ("Korsakoff", "rimsky-korsakov"), ("Ltizky", "leschetizky"), ("Scarlatti, D", "scarlatti-d"),
    ("Scarlatti, A", "scarlatti-a"), ("Sibelius, Jean (1865- 1957)", "sibelius"), ("Faure, Gabriel", "faure"),
])
def test_aliases(raw, slug):
    assert catalogue.find_composer(raw) == slug


def test_data_table_consistent():
    seen = {}
    for slug, c in catalogue_data.COMPOSERS.items():
        assert c["era"] in ("baroque", "classical", "romantic", "modern", None)
        assert slug == catalogue._slugify(slug)
        for a in [c["name"], *c["aliases"]]:
            k = catalogue._key(a)
            assert seen.setdefault(k, slug) == slug, f"alias {a!r} claimed twice"
    for wid, o in catalogue_data.WORKS.items():
        if "composer" in o:
            assert o["composer"] in catalogue_data.COMPOSERS
    for p in catalogue_data.PIANISTS.values():
        assert p["born"] is None or p["died"] is None or p["born"] < p["died"]


def test_unknown_v1_composer_is_kept_not_lost():
    v2 = catalogue.tidy({"composers": [{"name": "Zzyzx, Quentin", "slug": "q", "works": [w(1, "Rag 3-2")]}]})
    c = v2["composers"][0]
    assert c["name"] == "Quentin Zzyzx" and c["slug"] == "zzyzx" and c["era"] is None
    assert c["works"][0]["title"] == "Rag, Op. 3 No. 2"
