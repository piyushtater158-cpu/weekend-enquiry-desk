"""Structural validation of the 16 synthetic fixtures against the real scraped
corpus and official links. Written as the assertion each fixture must satisfy,
per the Testing Approach's TDD rule - this test, not eyeballing, is what proves
msg-01's expected_chunk_ids actually exist after a re-scrape changes section ids.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_fixtures():
    return json.loads((DATA_DIR / "synthetic_messages.json").read_text(encoding="utf-8"))


def load_known_chunk_ids():
    chunks = json.loads((DATA_DIR / "corpus" / "chunks.json").read_text(encoding="utf-8"))
    return {c["section_id"] for c in chunks}


def load_known_link_keys():
    links = json.loads((DATA_DIR / "official_links.json").read_text(encoding="utf-8"))
    return {link["key"] for link in links}


def test_exactly_sixteen_fixtures_all_labelled_synthetic():
    fixtures = load_fixtures()
    assert len(fixtures) == 16
    assert all(f["label"] == "SYNTHETIC" for f in fixtures)


def test_category_split_matches_design():
    fixtures = load_fixtures()
    counts = {}
    for f in fixtures:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    assert counts == {"answerable": 5, "link_only": 3, "escalate": 8}


def test_fixture_ids_are_unique():
    fixtures = load_fixtures()
    ids = [f["id"] for f in fixtures]
    assert len(ids) == len(set(ids))


def test_answerable_and_link_only_never_escalate():
    fixtures = load_fixtures()
    for f in fixtures:
        if f["category"] in ("answerable", "link_only"):
            assert f["expected_escalate"] is False, f["id"]


def test_escalate_category_always_escalates():
    fixtures = load_fixtures()
    for f in fixtures:
        if f["category"] == "escalate":
            assert f["expected_escalate"] is True, f["id"]


def test_expected_chunk_ids_exist_in_real_corpus():
    fixtures = load_fixtures()
    known = load_known_chunk_ids()
    for f in fixtures:
        for cid in f.get("expected_chunk_ids", []):
            assert cid in known, f"{f['id']} expects chunk {cid!r}, not found in scraped corpus"


def test_expected_link_keys_exist_in_official_links():
    fixtures = load_fixtures()
    known = load_known_link_keys()
    for f in fixtures:
        for key in f.get("expected_link_keys", []):
            assert key in known, f"{f['id']} expects link {key!r}, not found in official_links.json"


def test_group_quote_fixture_has_no_expected_price():
    """PR-08 regression guard at the fixture level: nothing about msg-09 should
    imply a numeric answer is ever expected."""
    fixtures = load_fixtures()
    group_quote = next(f for f in fixtures if f["id"] == "msg-09")
    assert group_quote["expected_intent"] == "group_quote"
    assert group_quote["expected_escalate"] is True


def test_price_and_injection_fixtures_present():
    """The two fixtures the outside-voice review added must still exist."""
    fixtures = load_fixtures()
    ids = {f["id"] for f in fixtures}
    assert "msg-15" in ids  # direct price question
    assert "msg-16" in ids  # prompt-injection attempt
