from app.tools import (
    corpus_max_accessed_at,
    escalate_for_human,
    get_official_links,
    published_weekends,
    search_knowledge_base,
)


def test_search_knowledge_base_returns_real_chunks():
    chunks = search_knowledge_base()
    assert len(chunks) > 0
    assert any(c["section_id"] == "pet-friendly" for c in chunks)
    assert all({"id", "section_id", "text", "url", "accessed_at", "source"} <= c.keys() for c in chunks)


def test_get_official_links_returns_six_rows():
    links = get_official_links()
    assert len(links) == 6
    keys = {link["key"] for link in links}
    assert keys == {"day_pass", "camping", "rooms", "email", "instagram", "whatsapp"}


def test_published_weekends_returns_real_events():
    events = published_weekends()
    assert len(events) == 3
    assert all("start_date" in e and "end_date" in e for e in events)


def test_corpus_max_accessed_at_is_a_datetime():
    stamp = corpus_max_accessed_at()
    assert stamp.year == 2026


def test_escalate_for_human_returns_structured_record():
    record = escalate_for_human(lead_id="abc-123", reason="group_quote")
    assert record["lead_id"] == "abc-123"
    assert record["reason"] == "group_quote"
    assert "escalated_at" in record
