"""Deterministic document and page classification.

Heuristics here are intentionally generic so the pipeline generalizes to unseen
earnings reports and call transcripts within v1 scope. No company-, executive-,
or sample-file-specific tokens belong in this module.
"""

from __future__ import annotations

import re

from earnings_extractor.ingest import PageText
from earnings_extractor.schema import (
    DocumentClassification,
    DocumentType,
    PageClassification,
    PageStyle,
)

NUMERIC_RE = re.compile(r"\b[\d,.]+%?\b")

# A speaker turn at the start of a line, e.g. "Jane Doe:" or "Operator:".
# Matches one to four capitalized tokens followed by a colon. This is a
# structural signal of a transcript, not a named individual.
SPEAKER_RE = re.compile(r"(?m)^[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3}\s*:")

# Phrases that signal a structured financial-statement / summary table. These
# are standard accounting headers, not specific to any one filer.
TABLE_MARKERS = (
    "$ in millions",
    "$ in thousands",
    "(unaudited)",
    "unaudited",
    "financial summary",
    "statement of operations",
    "consolidated statements",
    "consolidated balance",
)

# Structural signals of an earnings-call transcript page.
TRANSCRIPT_MARKERS = (
    "operator",
    "prepared remarks",
    "question-and-answer",
    "question and answer",
)

# Words that indicate the document as a whole is an earnings-call transcript.
# "transcript" alone is too broad: the assignment PDF can mention transcripts
# without itself being an earnings-call source document.
TRANSCRIPT_DOC_MARKERS = ("earnings call", "conference call")

# Assignment/admin PDFs can describe earnings-call transcripts without being
# source documents. Skip them before applying source-document heuristics.
NON_SOURCE_MARKERS = ("problem statement for candidate", "take home assignment")

# Generic signals that an investor-relations PDF is an earnings release/results
# document even if it does not use formal "consolidated statements" headings.
EARNINGS_RESULTS_RE = re.compile(
    r"\b(?:q[1-4]|first|second|third|fourth)\s+"
    r"(?:quarter\s+)?\d{4}\s+results\b",
    re.IGNORECASE,
)
EARNINGS_RELEASE_MARKERS = (
    "earnings release",
    "earnings results",
    "reported financial results",
    "reports financial results",
)
EARNINGS_METRIC_MARKERS = (
    "earnings per share",
    "diluted eps",
    "net income",
    "total revenues",
    "total revenue",
    "revenue up",
)

# Generic, template-aligned terms used to rank which pages carry the metrics we
# need to extract. All are standard financial vocabulary.
SELECT_MARKERS = (
    "financial summary",
    "$ in millions",
    "unaudited",
    "statement of operations",
    "income from operations",
    "operating income",
    "net income",
    "total revenue",
    "revenues",
    "gross margin",
    "operating expenses",
    "operating margin",
    "balance sheet",
    "cash flow",
    "earnings per share",
    "eps",
    "diluted",
    "dividend",
    "repurchase",
    "buyback",
)


def _numeric_density(text: str) -> float:
    lines = [line for line in text.splitlines() if line.strip()]
    return len(NUMERIC_RE.findall(text)) / max(1, len(lines))


def classify_page(page: PageText) -> PageClassification:
    text = page.text
    normalized = text.lower()
    numeric_density = _numeric_density(text)
    speaker_turns = len(SPEAKER_RE.findall(text))

    has_table_marker = any(marker in normalized for marker in TABLE_MARKERS)
    has_transcript_signal = speaker_turns >= 2 or any(
        marker in normalized for marker in TRANSCRIPT_MARKERS
    )

    if has_table_marker or numeric_density >= 2.5:
        style: PageStyle = "table_heavy"
    elif has_transcript_signal or numeric_density < 1.0:
        style = "narrative"
    else:
        style = "mixed"

    return PageClassification(
        page_number=page.page_number,
        style=style,
        char_count=page.char_count,
    )


def classify_document(pages: list[PageText]) -> DocumentClassification:
    if not pages:
        return DocumentClassification(
            source_file="",
            document_type="unknown",
            page_count=0,
            pages=[],
        )

    page_classifications = [classify_page(page) for page in pages]

    head = pages[: min(6, len(pages))]
    head_text = "\n".join(page.text.lower() for page in head)
    full_text = "\n".join(page.text.lower() for page in pages)
    total_speaker_turns = sum(len(SPEAKER_RE.findall(page.text)) for page in head)

    if any(marker in head_text for marker in NON_SOURCE_MARKERS):
        document_type: DocumentType = "unknown"
        return DocumentClassification(
            source_file=pages[0].source_file,
            document_type=document_type,
            page_count=len(pages),
            pages=page_classifications,
        )

    # Transcript detection stays head-scoped on purpose: an earnings report can
    # mention an upcoming "earnings call" without being a transcript, so a
    # whole-document scan for those phrases would cause false positives. We also
    # require structural evidence (speaker turns) alongside the phrase, so a
    # report that merely references an earnings call is not misread as one.
    has_transcript_phrase = any(
        marker in head_text for marker in TRANSCRIPT_DOC_MARKERS
    )
    has_transcript_structure = total_speaker_turns >= 4 and "operator:" in head_text
    has_transcript_section_marker = "operator:" in head_text or any(
        marker in head_text
        for marker in ("prepared remarks", "question-and-answer", "question and answer")
    )
    is_transcript = has_transcript_structure or (
        has_transcript_phrase
        and has_transcript_section_marker
        and total_speaker_turns >= 2
    )
    # Earnings reports and shareholder letters routinely place their financial
    # statements after several pages of narrative, so scan the whole document
    # for statement markers rather than only the head. This runs after the
    # transcript check, so a transcript that also contains statement tables
    # still classifies as a transcript.
    has_statement = any(marker in full_text for marker in TABLE_MARKERS)
    has_earnings_results_marker = EARNINGS_RESULTS_RE.search(head_text) is not None or (
        any(marker in head_text for marker in EARNINGS_RELEASE_MARKERS)
        and any(marker in head_text for marker in EARNINGS_METRIC_MARKERS)
    )

    if is_transcript:
        document_type: DocumentType = "earnings_call_transcript"
    elif has_statement or has_earnings_results_marker:
        document_type = "earnings_report"
    else:
        document_type = "unknown"

    return DocumentClassification(
        source_file=pages[0].source_file,
        document_type=document_type,
        page_count=len(pages),
        pages=page_classifications,
    )


def _page_relevance(text: str) -> float:
    """Higher score = more likely to contain the metrics we extract."""

    normalized = text.lower()
    marker_hits = sum(1 for marker in SELECT_MARKERS if marker in normalized)
    return _numeric_density(text) + 2 * marker_hits


def select_extraction_pages(
    pages: list[PageText], max_pages: int = 6
) -> list[PageText]:
    """Select the highest-signal pages for extraction.

    Always includes page 1 (cover / company identity) and then the pages with
    the strongest financial-metric relevance, ranked generically. Returns pages
    in physical order for stable, readable prompts.
    """

    if not pages:
        return []

    by_number = {page.page_number: page for page in pages}
    selected: dict[int, PageText] = {}

    if 1 in by_number:
        selected[1] = by_number[1]

    ranked = sorted(pages, key=lambda page: _page_relevance(page.text), reverse=True)
    for page in ranked:
        if len(selected) >= max_pages:
            break
        selected.setdefault(page.page_number, page)

    return [selected[number] for number in sorted(selected)]
