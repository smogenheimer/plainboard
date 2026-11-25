"""Hides what looks like a secret in text that leaves PlainBoard for strangers (a doc site's assistant, #119).

Shared by the app (every answer and tool result) and the runner (code a visitor's assistant reads). It catches the
common shapes of keys, tokens, private keys, passwords in URLs and assignments of secret-named variables; a secret
of no known shape passes. Nothing here is a guarantee: it is one layer besides not giving such a reader any of them.
"""

from __future__ import annotations

import re

HIDDEN = "[hidden]"

_PATTERNS = [
    # A private key block, also one still unfinished (streamed text): to its end.
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----(?:[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----|[\s\S]*$)"),
    re.compile(r"\b(?:sk-ant-|sk-proj-|sk-)[A-Za-z0-9_\-]{20,}"),  # Anthropic, OpenAI
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})"),  # GitHub
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),  # GitLab
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),  # Google
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),  # Slack
    re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"),  # Stripe
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-]{16,}=*"),
]
# user:password@ in a URL
_URL_LOGIN = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s:/@]+:[^\s@/]+@")
# NAME_WITH_SECRET = "literal" / : literal (a call or an expression stays: it is code, not a value)
_ASSIGNED = re.compile(
    r"(?i)\b([A-Za-z0-9_.\-]*(?:secret|token|passwd|password|api[_\-]?key|private[_\-]?key|access[_\-]?key|client[_\-]?secret)[A-Za-z0-9_\-]*)"
    r"(\"?\s*[:=]\s*)(?:([\"'])[^\"'\s]{6,}\3|(?![\"'])([A-Za-z0-9+/_\-.]{16,}=*)(?![\w(.\[]))"
)


def scrub(text: str) -> str:
    """The text with everything of a secret's shape replaced by ``[hidden]``."""
    if not text:
        return text
    for pattern in _PATTERNS:
        text = pattern.sub(HIDDEN, text)
    text = _URL_LOGIN.sub(lambda m: f"{m.group(1)}{HIDDEN}@", text)
    return _ASSIGNED.sub(lambda m: f"{m.group(1)}{m.group(2)}{HIDDEN}", text)
