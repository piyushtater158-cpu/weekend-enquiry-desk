"""Deterministic policy gate. Pure function over a structured draft - no model calls.

Eleven rules, each implemented as a small function taking (draft, context) and
returning a Violation or None, except PR-09 which appends a non-blocking note
instead of a violation. See config/policy_rules.yaml for the human-readable
metadata (severity, description) each rule_id maps to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Sentence:
    text: str
    cites: list[str] = field(default_factory=list)
    non_factual: bool = False


@dataclass
class Draft:
    sentences: list[Sentence]
    urls: list[str] = field(default_factory=list)
    intent: str = "faq"
    event_date: date | None = None
    mentions_camping_bundle_question: bool = False


@dataclass
class PolicyContext:
    known_chunk_ids: set[str] = field(default_factory=set)
    official_link_urls: set[str] = field(default_factory=set)
    published_weekends: list[tuple[date, date]] = field(default_factory=list)
    corpus_max_accessed_at: datetime | None = None
    corpus_stale_days: int = 45
    day_pass_url: str | None = None
    camping_url: str | None = None
    now: datetime | None = None


@dataclass
class Violation:
    rule_id: str
    severity: str
    detail: str


@dataclass
class PolicyResult:
    passed: bool
    violations: list[Violation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- PR-01: currency symbol/ISO code adjacent to digits, not any digit near any word ---

_CURRENCY_TOKEN = r"(?:AED|USD|EUR|GBP|dirhams?|درهم)"
_PR01_PATTERN = re.compile(
    rf"(?:[$£€]\s*\d+(?:\.\d+)?)"
    rf"|(?:\d+(?:\.\d+)?\s*[$£€])"
    rf"|(?:\b{_CURRENCY_TOKEN}\s*\d+(?:\.\d+)?\b)"
    rf"|(?:\d+(?:\.\d+)?\s*{_CURRENCY_TOKEN}\b)",
    re.IGNORECASE,
)


def _rule_pr01(draft: Draft, _ctx: PolicyContext) -> Violation | None:
    for s in draft.sentences:
        if _PR01_PATTERN.search(s.text):
            return Violation("PR-01", "critical", f"currency figure in: {s.text!r}")
    return None


# --- PR-02: availability claims ---

_AVAILABILITY_PHRASES = (
    "available", "sold out", "spots left", "fully booked", "we have space",
)


def _rule_pr02(draft: Draft, _ctx: PolicyContext) -> Violation | None:
    for s in draft.sentences:
        low = s.text.lower()
        for phrase in _AVAILABILITY_PHRASES:
            if phrase in low:
                return Violation("PR-02", "critical", f"availability claim ({phrase!r}) in: {s.text!r}")
    return None


# --- PR-03: affirmative yes/no answers need a citation ---

_AFFIRMATIVE_START = re.compile(r"^\s*yes\b", re.IGNORECASE)


def _rule_pr03(draft: Draft, _ctx: PolicyContext) -> Violation | None:
    for s in draft.sentences:
        if _AFFIRMATIVE_START.match(s.text) and not s.cites:
            return Violation("PR-03", "high", f"uncited affirmation: {s.text!r}")
    return None


# --- PR-04: every cited section id must exist in the corpus ---

def _rule_pr04(draft: Draft, ctx: PolicyContext) -> Violation | None:
    for s in draft.sentences:
        for cite in s.cites:
            if cite not in ctx.known_chunk_ids:
                return Violation("PR-04", "high", f"citation {cite!r} not found in corpus")
    return None


# --- PR-05: every factual sentence carries a citation ---

def _rule_pr05(draft: Draft, _ctx: PolicyContext) -> Violation | None:
    for s in draft.sentences:
        if not s.non_factual and not s.cites:
            return Violation("PR-05", "high", f"uncited factual sentence: {s.text!r}")
    return None


# --- PR-06: policy verbs need a citation ---

_POLICY_VERBS = ("refund", "cancel", "re-entry", "re entry", "age limit", "alcohol")


def _rule_pr06(draft: Draft, _ctx: PolicyContext) -> Violation | None:
    for s in draft.sentences:
        low = s.text.lower()
        for verb in _POLICY_VERBS:
            if verb in low and not s.cites:
                return Violation("PR-06", "critical", f"uncited policy claim ({verb!r}) in: {s.text!r}")
    return None


# --- PR-07: any URL in the draft must be on the whitelist ---

def _rule_pr07(draft: Draft, ctx: PolicyContext) -> Violation | None:
    for url in draft.urls:
        if url not in ctx.official_link_urls:
            return Violation("PR-07", "high", f"unlisted URL: {url!r}")
    return None


# --- PR-08: group quotes always escalate ---

def _rule_pr08(draft: Draft, _ctx: PolicyContext) -> Violation | None:
    if draft.intent == "group_quote":
        return Violation("PR-08", "critical", "group_quote intent must always escalate")
    return None


# --- PR-09: stale corpus (note, not a blocking violation) ---

def _check_pr09(_draft: Draft, ctx: PolicyContext) -> str | None:
    if ctx.corpus_max_accessed_at is None or ctx.now is None:
        return None
    age_days = (ctx.now - ctx.corpus_max_accessed_at).days
    if age_days > ctx.corpus_stale_days:
        return f"corpus is {age_days} days stale (threshold {ctx.corpus_stale_days})"
    return None


# --- PR-10: camping/day-pass bundling question must never get a bare yes/no ---

def _rule_pr10(draft: Draft, ctx: PolicyContext) -> Violation | None:
    if not draft.mentions_camping_bundle_question:
        return None
    has_bare_answer = any(_AFFIRMATIVE_START.match(s.text) for s in draft.sentences) or any(
        re.match(r"^\s*no\b", s.text, re.IGNORECASE) for s in draft.sentences
    )
    both_links = bool(ctx.day_pass_url) and bool(ctx.camping_url) and (
        ctx.day_pass_url in draft.urls and ctx.camping_url in draft.urls
    )
    if has_bare_answer or not both_links:
        return Violation(
            "PR-10", "high",
            "camping-included question answered without both ticket links and a "
            "confirm-with-the-team line",
        )
    return None


# --- PR-11: event date must fall inside a published weekend ---

def _rule_pr11(draft: Draft, ctx: PolicyContext) -> Violation | None:
    if draft.event_date is None:
        return None
    for start, end in ctx.published_weekends:
        if start <= draft.event_date <= end:
            return None
    return Violation(
        "PR-11", "medium",
        f"{draft.event_date} falls outside every published weekend",
    )


_BLOCKING_RULES = (
    _rule_pr01, _rule_pr02, _rule_pr03, _rule_pr04, _rule_pr05,
    _rule_pr06, _rule_pr07, _rule_pr08, _rule_pr10, _rule_pr11,
)


def evaluate(draft: Draft, context: PolicyContext) -> PolicyResult:
    violations: list[Violation] = []
    for rule in _BLOCKING_RULES:
        v = rule(draft, context)
        if v is not None:
            violations.append(v)

    notes: list[str] = []
    note = _check_pr09(draft, context)
    if note is not None:
        notes.append(note)

    return PolicyResult(passed=not violations, violations=violations, notes=notes)
