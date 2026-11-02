"""Secret redaction (Phase 7 security controls).

Defence in depth for the AI layer:

* User chat text is UNTRUSTED: anything a user types (including pasted
  API keys, passwords, broker credentials) must never be forwarded to a
  third-party LLM provider or written to application logs verbatim.
* ``redact_text`` replaces common secret shapes with a fixed marker
  before text is sent to the provider or logged.

Known limits (documented honestly): redaction is pattern-based. It
catches the common credential shapes (bearer tokens, ``sk-`` style keys,
passwords in URLs, key/value assignments). It cannot guarantee that
every conceivable secret format is caught — users must never paste
credentials into the research chat, and the platform never asks for
them (the provider key lives in server-side configuration only).
"""

from __future__ import annotations

import re

_REDACTED = "[REDACTED]"

# Ordered: first match wins. Patterns are conservative (min lengths) to
# avoid mangling ordinary prose/numbers.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Authorization headers / bearer tokens
    (
        "bearer",
        re.compile(r"(?i)\b(bearer|authorization)\b[\s:]*[A-Za-z0-9._\-+/=]{12,}"),
    ),
    # OpenAI-style / generic secret prefixes
    ("key_prefix", re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9._\-]{10,}")),
    # key = value assignments for credential-ish keys
    (
        "key_value",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|"
            r"credential[s]?|login_password|mt5_password)\b[\s]*[:=][\s]*"
            r"[^\s,;\"']{6,}"
        ),
    ),
    # URL credentials https://user:password@host
    ("url_credentials", re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")),
    # Long hex/base64-ish blobs (>= 32 chars) — likely tokens
    (
        "long_blob",
        re.compile(r"\b[A-Za-z0-9+/=_\-]{32,}\b"),
    ),
    # JWTs
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_/+\-]{10,}\.[A-Za-z0-9_/+\-]{10,}\.[A-Za-z0-9_/+\-]{10,}")),
)


def redact_text(text: str) -> str:
    """Return ``text`` with credential-shaped substrings replaced."""
    if not text:
        return text
    result = text
    for _, pattern in _PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result


__all__ = ["redact_text"]
