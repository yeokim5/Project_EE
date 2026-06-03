"""Deterministic company identity resolution from source text and metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass

from earnings_extractor.ingest import PageText
from earnings_extractor.schema import DocumentType, MetricRow


@dataclass(frozen=True)
class IdentityCandidate:
    name: str
    ticker: str | None
    source_page: int
    source_quote: str
    needs_review: bool
    review_reason: str | None


GENERIC_COMPANY_SUFFIXES = (
    "inc",
    "inc.",
    "llc",
    "corp",
    "corporation",
    "company",
    "co.",
    "plc",
)

# Filename/metadata tokens flatten punctuation, so "U.S. Bancorp" arrives as
# "us bancorp" and a bare "us" can spuriously match the pronoun on a page.
# This maps the flattened form back to the canonical display name. Extend as new
# punctuation-sensitive issuers appear.
KNOWN_COMPANY_NAMES = {
    "us bancorp": "U.S. Bancorp",
    "u s bancorp": "U.S. Bancorp",
    "us bancorp.": "U.S. Bancorp",
}

# A lone token this short or shorter is never a company name on its own; it is a
# pronoun/abbreviation ("us", "we") that must not become an identity.
MIN_STANDALONE_NAME_LEN = 3


def resolve_company_identity(
    pages: list[PageText],
    metadata: dict[str, str],
    source_file: str,
    document_type: DocumentType,
) -> IdentityCandidate | None:
    """Resolve identity with real page evidence when possible.

    Metadata and filenames propose candidates; full page text must support the
    final citation or the row remains review-flagged.
    """

    title_candidate = _candidate_from_transcript_title(pages)
    if title_candidate is not None:
        return title_candidate

    candidates = _metadata_candidates(metadata)
    candidates.extend(_filename_candidates(source_file))
    candidates = _dedupe(
        _canonicalize_known_name(candidate)
        for candidate in candidates
        if _is_plausible_name(candidate)
    )

    for candidate in candidates:
        evidence = _find_on_page_evidence(candidate, pages)
        if evidence is not None:
            page_number, quote, evidence_name = evidence
            return IdentityCandidate(
                name=evidence_name,
                ticker=None,
                source_page=page_number,
                source_quote=quote,
                needs_review=False,
                review_reason=None,
            )

    if candidates:
        return IdentityCandidate(
            name=candidates[0],
            ticker=None,
            source_page=pages[0].page_number if pages else 1,
            source_quote="Company identity inferred from PDF metadata or filename",
            needs_review=True,
            review_reason=(
                "Company identity was inferred from metadata/filename and was "
                "not found as an explicit on-page source quote."
            ),
        )

    if document_type == "earnings_call_transcript":
        return _fallback_transcript_identity(pages)
    return None


def apply_company_identity(
    metrics: list[MetricRow],
    identity: IdentityCandidate | None,
) -> None:
    if identity is None:
        return

    for metric in metrics:
        if metric.company is None:
            metric.company = identity.name
        if metric.ticker is None and identity.ticker is not None:
            metric.ticker = identity.ticker

    row = next((m for m in metrics if m.metric_name == "Company Name"), None)
    if row is None:
        return

    row.value = identity.name
    row.company = identity.name
    if identity.ticker is not None:
        row.ticker = identity.ticker
    row.source_page = identity.source_page
    row.source_quote = identity.source_quote
    if identity.needs_review:
        row.confidence = min(row.confidence, 0.6)
        row.needs_review = True
        row.review_reason = _append_reason(row.review_reason, identity.review_reason)
    elif row.review_reason is None or "not stated" in row.review_reason.lower():
        row.confidence = max(row.confidence, 0.9)
        row.needs_review = False
        row.review_reason = None


def _candidate_from_transcript_title(pages: list[PageText]) -> IdentityCandidate | None:
    pattern = re.compile(
        r"\b([A-Z][A-Za-z&.' -]{1,60}?)\s+"
        r"(?:First|Second|Third|Fourth|Q[1-4])\s+Quarter\s+\d{4}\s+Earnings Call\b"
    )
    for page in pages:
        match = pattern.search(page.text)
        if match:
            name = _clean_company_name(match.group(1))
            if name:
                return IdentityCandidate(
                    name=name,
                    ticker=None,
                    source_page=page.page_number,
                    source_quote=match.group(0),
                    needs_review=False,
                    review_reason=None,
                )
    return None


def _metadata_candidates(metadata: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    for key in ("Author", "Company", "Creator"):
        value = metadata.get(key, "")
        if "@" in value:
            domain = value.split("@", 1)[1].split(".", 1)[0]
            candidates.append(domain.title())
        else:
            cleaned = _clean_company_name(value)
            if cleaned:
                candidates.append(cleaned)
    return _dedupe(candidates)


def _filename_candidates(source_file: str) -> list[str]:
    stem = source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    candidates = []
    tokens = [token for token in re.split(r"[-_\s]+", stem) if token]
    company_tokens: list[str] = []
    for token in tokens:
        lower = token.lower()
        if (
            re.fullmatch(r"q[1-4](?:\d{4})?", lower)
            or re.fullmatch(r"[1-4]q\d{2,4}", lower)
            or re.fullmatch(r"\d{4}", lower)
            or lower
            in {
                "annual",
                "earnings",
                "earning",
                "financial",
                "press",
                "release",
                "report",
                "results",
                "update",
            }
        ):
            break
        company_tokens.append(token)

    if company_tokens:
        candidate = " ".join(company_tokens)
        if not re.fullmatch(r"[A-Z]{1,5}", candidate):
            candidates.append(candidate.title())

    first_token = tokens[0] if tokens else ""
    if first_token and not re.fullmatch(r"[A-Z]{1,5}", first_token):
        candidates.append(first_token.title())
    return candidates


def _find_on_page_evidence(
    candidate: str, pages: list[PageText]
) -> tuple[int, str, str] | None:
    if not candidate:
        return None
    pattern = re.compile(rf"\b{re.escape(candidate)}(?:['’]s)?\b", re.IGNORECASE)
    for page in pages:
        for line in page.text.splitlines():
            match = pattern.search(line)
            if match:
                return (
                    page.page_number,
                    _trim_quote(line),
                    _canonicalize_matched_name(match.group(0)),
                )
    return None


def _canonicalize_matched_name(value: str) -> str:
    value = re.sub(r"['’]s$", "", value.strip())
    if value.isupper():
        return value.title()
    return value


def _fallback_transcript_identity(pages: list[PageText]) -> IdentityCandidate | None:
    for page in pages[:2]:
        match = re.search(r"\b([A-Z][A-Za-z&.' -]{1,40}) Earnings Call\b", page.text)
        if match:
            name = _clean_company_name(match.group(1))
            return IdentityCandidate(
                name=name,
                ticker=None,
                source_page=page.page_number,
                source_quote=match.group(0),
                needs_review=False,
                review_reason=None,
            )
    return None


def _canonicalize_known_name(value: str) -> str:
    """Map a flattened candidate back to its canonical display name, if known."""

    key = re.sub(r"\s+", " ", value).strip().lower()
    return KNOWN_COMPANY_NAMES.get(key, value)


def _is_plausible_name(value: str) -> bool:
    """Reject candidates too short or generic to stand alone as a company name."""

    cleaned = value.strip()
    if not cleaned:
        return False
    # Known multi-word names (e.g. "us bancorp") are always allowed through.
    if re.sub(r"\s+", " ", cleaned).lower() in KNOWN_COMPANY_NAMES:
        return True
    # A single short token like "Us"/"We" is a pronoun, not an issuer name.
    if " " not in cleaned and len(cleaned) < MIN_STANDALONE_NAME_LEN:
        return False
    return True


def _clean_company_name(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\b(?:Microsoft|Adobe|PDF|Word|InDesign)\b.*", "", value).strip()
    tokens = value.split()
    while tokens and tokens[-1].lower() in GENERIC_COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _trim_quote(line: str, limit: int = 220) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


def _append_reason(existing: str | None, reason: str | None) -> str | None:
    if not reason:
        return existing
    if not existing:
        return reason
    if reason in existing:
        return existing
    return f"{existing}; {reason}"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            deduped.append(value)
    return deduped
