"""Pick the parts of a long study guide that matter for the current question.

A final-exam study guide is far larger than the coach prompt budget (see
_MAX_CONTEXT_CHARS in interview_live). Sending it whole costs latency — with
thinking disabled, time-to-first-token is dominated by prefill — and dilutes the
model with dozens of unrelated topics.

Instead we split the guide into sections and send only the ones that match what
the examiner just asked. Markdown headings give us the section boundaries for
free; plain text falls back to blank-line paragraphs.

Scoring is BM25 over the section corpus. No embeddings, no network, no extra
dependency: the guide is small enough that a fresh index per call is cheaper
than any caching we could add, and it keeps the module a pure function.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

# Query terms carrying no topical signal. Both languages: the guide is usually
# Spanish while the examiner's speech arrives as English STT.
_STOPWORDS = frozenset(
    """
    a al algo alguna algunas alguno algunos ante antes como con contra cual cuales cuando
    de del desde donde dos el ella ellas ellos en entre era eran es esa esas ese eso esos
    esta estan estas este esto estos ha hace hacia han hasta hay la las le les lo los mas
    me mi mis mucho muy nada ni no nos nosotros o os otra otro para pero poco por porque
    que quien quienes se ser si sin sobre son su sus también tanto te tiene tienen todo
    todos tu tus un una uno unos usted ustedes va vamos ver y ya
    about after all also am an and any are as at be been being but by can could did do
    does doing done for from had has have he her here hers him his how i if in into is it
    its me might must my no nor not of on once only or other our out over own same shall
    she should so some such than that the their them then there these they this those to
    too under until up us very was we were what when where which while who whom why will
    with would you your
    """.split()
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_MIN_TOKEN_LEN = 3
_BM25_K1 = 1.5
_BM25_B = 0.75


def _normalize(text: str) -> str:
    """Lowercase and strip accents so 'función' matches 'funcion'."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def tokenize(text: str) -> list[str]:
    return [
        tok
        for tok in _TOKEN_RE.findall(_normalize(text))
        if len(tok) >= _MIN_TOKEN_LEN and tok not in _STOPWORDS
    ]


@dataclass
class Section:
    """One retrievable slice of the study guide."""

    heading: str
    body: str
    tokens: list[str] = field(default_factory=list)

    def render(self) -> str:
        if not self.heading:
            return self.body
        return f"{self.heading}\n{self.body}".strip()


def split_sections(context: str) -> list[Section]:
    """Split a study guide into sections, preferring markdown headings.

    Headings are kept as a breadcrumb ('Unidad 3 > Herencia') so a retrieved
    section still says which unit it belongs to once it is out of context.
    """
    text = (context or "").strip()
    if not text:
        return []

    lines = text.splitlines()
    if not any(_HEADING_RE.match(line) for line in lines):
        return _split_paragraphs(text)

    sections: list[Section] = []
    trail: list[str] = []  # heading text indexed by level-1
    current: list[str] = []
    heading = ""

    def flush() -> None:
        body = "\n".join(current).strip()
        if body or heading:
            sections.append(Section(heading=heading, body=body))

    for line in lines:
        match = _HEADING_RE.match(line)
        if not match:
            current.append(line)
            continue
        flush()
        level = len(match.group(1))
        title = match.group(2)
        del trail[level - 1 :]
        trail.append(title)
        heading = " > ".join(trail)
        current = []
    flush()

    return [s for s in sections if s.body or s.heading]


def _split_paragraphs(text: str) -> list[Section]:
    """Fallback for guides pasted as plain text: blank-line paragraphs."""
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]
    return [Section(heading="", body=chunk) for chunk in chunks]


def _score_sections(sections: list[Section], query_tokens: list[str]) -> list[float]:
    """BM25 score of every section against the query."""
    total = len(sections)
    avg_len = sum(len(s.tokens) for s in sections) / total

    doc_freq: dict[str, int] = {}
    for term in set(query_tokens):
        doc_freq[term] = sum(1 for s in sections if term in s.tokens)

    scores = []
    for section in sections:
        length = len(section.tokens) or 1
        score = 0.0
        for term in set(query_tokens):
            freq = section.tokens.count(term)
            if not freq:
                continue
            # +0.5/+1 smoothing keeps the idf positive for terms present in
            # every section, so a one-section guide still scores above zero.
            idf = math.log(1 + (total - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            norm = _BM25_K1 * (1 - _BM25_B + _BM25_B * length / avg_len)
            score += idf * (freq * (_BM25_K1 + 1)) / (freq + norm)
        scores.append(score)
    return scores


def select_context(context: str, query: str, max_chars: int) -> str:
    """Return the slice of `context` most relevant to `query`, within budget.

    Falls back to a plain head-truncation when the guide already fits, when
    there is no query, or when nothing matches — the coach must never be left
    without context just because retrieval came up empty.
    """
    text = (context or "").strip()
    if len(text) <= max_chars:
        return text

    query_tokens = tokenize(query)
    sections = split_sections(text)
    if not query_tokens or not sections:
        return _head(text, max_chars)

    for section in sections:
        section.tokens = tokenize(section.render())

    scores = _score_sections(sections, query_tokens)
    ranked = sorted(
        (i for i, score in enumerate(scores) if score > 0),
        key=lambda i: scores[i],
        reverse=True,
    )
    if not ranked:
        return _head(text, max_chars)

    # Re-sort the winners into document order so the guide still reads top-down.
    picked: list[int] = []
    used = 0
    for idx in ranked:
        rendered = sections[idx].render()
        cost = len(rendered) + 2  # separator
        if used + cost > max_chars:
            continue
        picked.append(idx)
        used += cost
    if not picked:
        return _head(sections[ranked[0]].render(), max_chars)

    picked.sort()
    return "\n\n".join(sections[i].render() for i in picked)


def _head(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " […]"
