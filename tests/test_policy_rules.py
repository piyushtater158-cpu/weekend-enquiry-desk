"""TDD: these tests are written before app/policy.py's real rule logic exists.
Run against the stub first (expected: red), then app/policy.py is implemented
until every test here is green. One fires-on-a-positive and one
stays-quiet-on-a-negative test per rule, per the design doc's Testing Approach.
"""
from datetime import date, datetime, timedelta

from app.policy import Draft, PolicyContext, Sentence, evaluate

KNOWN_IDS = {"dogs-welcome", "kids-welcome", "camping", "hours"}
LINKS = {
    "https://sevn.ly/xvKcpSJX",  # day pass
    "https://sevn.ly/xshc0Y2s",  # camping
    "https://live.ipms247.com/booking/book-rooms-ummalquwainbeachhotel",
}
PUBLISHED_WEEKENDS = [
    (date(2026, 11, 14), date(2026, 11, 15)),
    (date(2026, 11, 21), date(2026, 11, 22)),
    (date(2026, 11, 28), date(2026, 11, 29)),
]


def base_context(**overrides) -> PolicyContext:
    defaults = dict(
        known_chunk_ids=KNOWN_IDS,
        official_link_urls=LINKS,
        published_weekends=PUBLISHED_WEEKENDS,
        corpus_max_accessed_at=datetime(2026, 11, 13),
        corpus_stale_days=45,
        day_pass_url="https://sevn.ly/xvKcpSJX",
        camping_url="https://sevn.ly/xshc0Y2s",
        now=datetime(2026, 11, 13),
    )
    defaults.update(overrides)
    return PolicyContext(**defaults)


def violated(result, rule_id: str) -> bool:
    return any(v.rule_id == rule_id for v in result.violations)


# --- PR-01 no_currency ---

def test_pr01_fires_on_currency_amount():
    draft = Draft(sentences=[Sentence("Day passes are AED 150 per person.", cites=["hours"])])
    result = evaluate(draft, base_context())
    assert violated(result, "PR-01")


def test_pr01_quiet_on_legitimate_digits():
    draft = Draft(sentences=[Sentence(
        "DJs run noon to 11pm on Saturdays, Nov 14, for a group of 20.",
        cites=["hours"],
    )])
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-01")


# --- PR-02 no_availability_claim ---

def test_pr02_fires_on_availability_claim():
    draft = Draft(sentences=[Sentence("Yes, we have space this Saturday!", cites=["hours"])])
    result = evaluate(draft, base_context())
    assert violated(result, "PR-02")


def test_pr02_quiet_without_availability_claim():
    draft = Draft(sentences=[Sentence(
        "You can grab day passes through SevenRooms.", cites=["hours"],
    )])
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-02")


# --- PR-03 no_unsourced_affirmation ---

def test_pr03_fires_on_uncited_affirmation():
    draft = Draft(sentences=[Sentence("Yes, dogs are welcome.", cites=[])])
    result = evaluate(draft, base_context())
    assert violated(result, "PR-03")


def test_pr03_quiet_on_cited_affirmation():
    draft = Draft(sentences=[Sentence("Yes, dogs are welcome.", cites=["dogs-welcome"])])
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-03")


# --- PR-04 citation_exists ---

def test_pr04_fires_on_unknown_citation():
    draft = Draft(sentences=[Sentence("Kids are welcome too.", cites=["not-a-real-chunk"])])
    result = evaluate(draft, base_context())
    assert violated(result, "PR-04")


def test_pr04_quiet_on_known_citation():
    draft = Draft(sentences=[Sentence("Kids are welcome too.", cites=["kids-welcome"])])
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-04")


# --- PR-05 claim_coverage ---

def test_pr05_fires_on_uncited_factual_sentence():
    draft = Draft(sentences=[Sentence("Dogs are welcome and get water bowls.", cites=[])])
    result = evaluate(draft, base_context())
    assert violated(result, "PR-05")


def test_pr05_quiet_on_non_factual_sentence():
    draft = Draft(sentences=[Sentence(
        "Looking forward to seeing you this weekend!", cites=[], non_factual=True,
    )])
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-05")


# --- PR-06 no_invented_policy ---

def test_pr06_fires_on_uncited_policy_verb():
    draft = Draft(sentences=[Sentence("You can get a full refund any time.", cites=[])])
    result = evaluate(draft, base_context())
    assert violated(result, "PR-06")


def test_pr06_quiet_on_cited_policy_verb():
    draft = Draft(sentences=[Sentence("You can get a full refund.", cites=["hours"])])
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-06")


# --- PR-07 link_whitelist ---

def test_pr07_fires_on_unlisted_url():
    draft = Draft(
        sentences=[Sentence("Book here.", cites=["hours"])],
        urls=["https://totally-fake-domain.example/tickets"],
    )
    result = evaluate(draft, base_context())
    assert violated(result, "PR-07")


def test_pr07_quiet_on_whitelisted_url():
    draft = Draft(
        sentences=[Sentence("Book here.", cites=["hours"])],
        urls=["https://sevn.ly/xvKcpSJX"],
    )
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-07")


# --- PR-08 group_quote_must_escalate ---

def test_pr08_fires_on_group_quote_intent():
    draft = Draft(sentences=[Sentence("Here is your quote.", cites=["hours"])], intent="group_quote")
    result = evaluate(draft, base_context())
    assert violated(result, "PR-08")


def test_pr08_quiet_on_other_intent():
    draft = Draft(sentences=[Sentence("Here are the ticket links.", cites=["hours"])], intent="tickets")
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-08")


# --- PR-09 stale_corpus (note, not a blocking violation) ---

def test_pr09_fires_note_when_corpus_stale():
    ctx = base_context(
        corpus_max_accessed_at=datetime(2026, 1, 1),
        now=datetime(2026, 11, 13),
    )
    draft = Draft(sentences=[Sentence("Dogs are welcome.", cites=["dogs-welcome"])])
    result = evaluate(draft, ctx)
    assert any("stale" in note.lower() or "fresh" in note.lower() for note in result.notes)
    assert not violated(result, "PR-09")  # note, never a blocking violation


def test_pr09_quiet_when_corpus_fresh():
    ctx = base_context(
        corpus_max_accessed_at=datetime(2026, 11, 1),
        now=datetime(2026, 11, 13),
    )
    draft = Draft(sentences=[Sentence("Dogs are welcome.", cites=["dogs-welcome"])])
    result = evaluate(draft, ctx)
    assert result.notes == []


# --- PR-10 camping_not_bundled ---

def test_pr10_fires_on_bare_yes_no_for_bundle_question():
    draft = Draft(
        sentences=[Sentence("Yes, camping is included.", cites=[])],
        urls=[],
        mentions_camping_bundle_question=True,
    )
    result = evaluate(draft, base_context())
    assert violated(result, "PR-10")


def test_pr10_quiet_with_both_links_and_no_bare_answer():
    draft = Draft(
        sentences=[Sentence(
            "Day passes and camping are separate tickets - here are both links, "
            "and we'll confirm with the team if you want to combine them.",
            cites=["camping"],
        )],
        urls=["https://sevn.ly/xvKcpSJX", "https://sevn.ly/xshc0Y2s"],
        mentions_camping_bundle_question=True,
    )
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-10")


# --- PR-11 date_outside_published_weekends ---

def test_pr11_fires_on_date_outside_published_weekends():
    draft = Draft(
        sentences=[Sentence("See you then!", cites=["hours"])],
        event_date=date(2026, 12, 25),
    )
    result = evaluate(draft, base_context())
    assert violated(result, "PR-11")


def test_pr11_quiet_on_published_weekend_date():
    draft = Draft(
        sentences=[Sentence("See you then!", cites=["hours"])],
        event_date=date(2026, 11, 14),
    )
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-11")


def test_pr11_quiet_when_no_date_resolved():
    draft = Draft(sentences=[Sentence("Here's the FAQ.", cites=["hours"])], event_date=None)
    result = evaluate(draft, base_context())
    assert not violated(result, "PR-11")
