"""Inbound PII filter for listener connector messages.

Runs on text arriving from listener connectors (gmail_imap, telegram_user,
discord, …) *before* it is stored in the database or handed to the LLM.

This is distinct from ``mask_secrets`` (which redacts credentials in tool
*output*).  This module redacts personal identifiable information in
*inbound* message text.

Replacements:
    [EMAIL]       — email addresses
    [PHONE]       — phone / mobile numbers
    [CREDIT_CARD] — 13–19 digit card numbers (Luhn-style spacing)
    [SSN]         — US Social Security Numbers  (XXX-XX-XXXX)
    [IBAN]        — International Bank Account Numbers

Names are not masked here — doing so reliably requires an NLP model and
produces too many false positives on regular text.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Each entry is (pattern, replacement_label).
# Patterns are ordered from most-specific to least-specific to avoid
# partial matches swallowing context.
_RULES: List[Tuple[re.Pattern, str]] = [
    # Credit cards — 13–19 digits, optionally grouped by spaces or dashes
    (
        re.compile(
            r"\b(?:4[0-9]{12}(?:[0-9]{3,6})?|"          # Visa
            r"5[1-5][0-9]{14}|"                           # Mastercard
            r"3[47][0-9]{13}|"                            # Amex
            r"6(?:011|5[0-9]{2})[0-9]{12,15}|"           # Discover
            r"(?:[0-9][ -]?){13,19})"                     # generic grouped
            r"\b"
        ),
        "[CREDIT_CARD]",
    ),
    # US SSN  XXX-XX-XXXX
    (
        re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        "[SSN]",
    ),
    # IBAN  (2-letter country code + 2 check digits + up to 30 alphanum, spaced or not)
    (
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{11,30}\b"),
        "[IBAN]",
    ),
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL]",
    ),
    # Phone numbers — various international / local formats
    # Matches: +1-800-555-1234, (800) 555-1234, 800.555.1234, +44 20 7946 0958, …
    (
        re.compile(
            r"(?<!\d)"
            r"(\+?\d{1,3}[\s\-.]?)?"          # optional country code
            r"(\(?\d{2,4}\)?[\s\-.]?)"        # area code
            r"\d{3,4}[\s\-.]?\d{3,4}"         # local number
            r"(?!\d)"
        ),
        "[PHONE]",
    ),
]


def filter_pii(text: str) -> str:
    """Return *text* with PII patterns replaced by labelled placeholders.

    Safe to call on any string; returns the input unchanged if no patterns
    match or if the input is empty.
    """
    if not text:
        return text
    for pattern, label in _RULES:
        text = pattern.sub(label, text)
    return text
