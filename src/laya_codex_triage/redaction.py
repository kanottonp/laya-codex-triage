"""Pure prompt redaction performed before storage or inference."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

MAX_PROMPT_CHARS = 4_000
REDACTION_VERSION = "1"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.DOTALL,
)
_AUTH = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_API_KEY = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
    r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?P<name>(?=[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY))"
    r"[A-Z_][A-Z0-9_]*)\s*=\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s;,]+)"
)
_URL_USERINFO = re.compile(r"(?i)(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s:]+(?::[^/@\s]*)?@")


@dataclass(frozen=True, slots=True)
class RedactedPrompt:
    text: str
    redaction_count: int
    truncated: bool
    version: str = REDACTION_VERSION


def redact_prompt(text: str, patterns: Sequence[str] = ()) -> RedactedPrompt:
    """Normalize, redact known secrets, then truncate without splitting a marker."""

    redacted = unicodedata.normalize("NFC", text)
    count = 0

    for pattern, replacement in (
        (_PRIVATE_KEY, "[REDACTED_PRIVATE_KEY]"),
        (_URL_USERINFO, r"\g<scheme>[REDACTED_URL_CREDENTIALS]@"),
        (_AUTH, "[REDACTED_AUTH]"),
        (_API_KEY, "[REDACTED_API_KEY]"),
    ):
        redacted, replacements = pattern.subn(replacement, redacted)
        count += replacements

    redacted, replacements = _CREDENTIAL_ASSIGNMENT.subn(
        lambda match: f"{match.group('name')}=[REDACTED_CREDENTIAL]", redacted
    )
    count += replacements

    for custom_pattern in patterns:
        redacted, replacements = re.subn(custom_pattern, "[REDACTED_CUSTOM]", redacted)
        count += replacements

    truncated = len(redacted) > MAX_PROMPT_CHARS
    if truncated:
        redacted = _truncate_without_splitting_marker(redacted)

    return RedactedPrompt(text=redacted, redaction_count=count, truncated=truncated)


def _truncate_without_splitting_marker(text: str) -> str:
    candidate = text[:MAX_PROMPT_CHARS]
    for marker_match in re.finditer(r"\[REDACTED_[A-Z_]+\]", text):
        if marker_match.start() < MAX_PROMPT_CHARS <= marker_match.end():
            marker = marker_match.group(0)
            prefix_length = max(0, MAX_PROMPT_CHARS - len(marker))
            return text[:prefix_length] + marker
    return candidate
