"""
Shared email-body helpers: stripping quoted reply history + signatures.

Email clients echo the entire prior thread into each reply. That bloats the
LLM context with our own templates and muddies intent classification, so we
cut the reply at the first quote boundary before it ever reaches a model.
"""

import re

_WROTE_RE = re.compile(r"^On\b.+\bwrote:$", re.IGNORECASE)
_ORIGINAL_MSG_RE = re.compile(
    r"^(?:[-_=*]{5,}\s*(?:original|forwarded) message\s*[-_=*]{5,}|"
    r"---?[- ]?original[- ]?message---?|"
    r"(?:original|forwarded)\s+message|"
    r"(?:begin|start)\s+forwarded\s+message)$",
    re.IGNORECASE,
)
_OUTLOOK_SEP_RE = re.compile(r"^_{5,}\s*$")
_REPLY_ABOVE_RE = re.compile(r"^Reply above this line$", re.IGNORECASE)
_SENT_FROM_RE = re.compile(
    r"^Sent from (?:my )?(?:iPhone|iPad|Android|Outlook|BlackBerry)\b",
    re.IGNORECASE,
)
_SIG_SEP_RE = re.compile(r"^--\s*$")


def strip_quotes(body: str) -> str:
    """Return only the new reply text — no quoted history, no signature."""
    if not body:
        return ""
    cut = []
    for line in body.splitlines():
        t = line.strip()
        if t.startswith(">"):
            break
        if _WROTE_RE.match(t) or _ORIGINAL_MSG_RE.match(t):
            break
        if (_OUTLOOK_SEP_RE.match(t) or _REPLY_ABOVE_RE.match(t)
                or _SENT_FROM_RE.match(t) or _SIG_SEP_RE.match(t)):
            break
        cut.append(line)
    cleaned = "\n".join(cut).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned)