"""Shared parsing helpers for numeric claims in evidence-backed reports."""

from __future__ import annotations

from collections.abc import Iterable
import math
import re


_NUMERIC_CLAIM_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])-?(?:"
    r"\d+(?:\.\d+)?[eE][+-]?\d+|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\d+(?:\.\d+)?|\.\d+"
    r")%?(?![A-Za-z0-9_])"
)
_SENTENCE_BOUNDARY_PATTERN = re.compile(
    r"(?<=[。！？!?；;])|(?<=\.)(?=\s|$)|(?=\r?\n)"
)


def numeric_claims(text: str) -> tuple[str, ...]:
    """Return distinct complete numeric tokens in source order."""
    seen: list[str] = []
    for match in _NUMERIC_CLAIM_PATTERN.finditer(text):
        token = match.group(0)
        if token not in seen:
            seen.append(token)
    return tuple(seen)


def numeric_value(token: str) -> float:
    """Convert a report token to the value used by evidence matching."""
    return float(token.rstrip("%").replace(",", ""))


def matches_verified_value(
    token: str,
    verified_values: Iterable[float],
    *,
    relative_tolerance: float = 1e-6,
    absolute_tolerance: float = 1e-9,
) -> bool:
    """Return whether a token is equal to one of the verified values."""
    try:
        value = numeric_value(token)
    except (TypeError, ValueError):
        return False
    return any(
        math.isclose(
            value,
            verified_value,
            rel_tol=relative_tolerance,
            abs_tol=absolute_tolerance,
        )
        for verified_value in verified_values
    )


def replace_unsupported_numeric_claims(
    text: str,
    unsupported_numbers: Iterable[str],
    *,
    replacement: str = "【待确认数字】",
) -> str:
    """Replace only complete unsupported numeric tokens, never substrings."""
    unsupported = frozenset(str(item) for item in unsupported_numbers if item)
    if not unsupported:
        return text

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return replacement if token in unsupported else token

    return _NUMERIC_CLAIM_PATTERN.sub(replace, text)


def remove_unsupported_numeric_sentences(
    text: str, unsupported_numbers: Iterable[str]
) -> str:
    """Drop narrative clauses containing unverified numeric claims."""
    unsupported = frozenset(str(item) for item in unsupported_numbers if item)
    if not unsupported or not text:
        return text

    sanitized_sentences: list[str] = []
    for sentence in _SENTENCE_BOUNDARY_PATTERN.split(text):
        clauses_and_separators = re.split(r"([，,；;、])", sentence)
        clauses = clauses_and_separators[::2]
        separators = clauses_and_separators[1::2]
        kept_indices = [
            index
            for index, clause in enumerate(clauses)
            if not unsupported.intersection(numeric_claims(clause))
        ]
        if not kept_indices:
            continue
        rebuilt: list[str] = []
        previous_index: int | None = None
        for index in kept_indices:
            if previous_index is not None and previous_index < len(separators):
                rebuilt.append(separators[previous_index])
            rebuilt.append(clauses[index])
            previous_index = index
        sanitized_sentences.append("".join(rebuilt))
    return "".join(sanitized_sentences)


__all__ = [
    "matches_verified_value",
    "numeric_claims",
    "numeric_value",
    "remove_unsupported_numeric_sentences",
    "replace_unsupported_numeric_claims",
]
