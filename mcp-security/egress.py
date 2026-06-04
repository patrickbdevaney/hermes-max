"""egress.py — classify the network destinations a shell command would reach (mcp-security).

Pattern-based, NO execution. Returns {kind, target} pairs the executor or operator can review
before running a command. Conservative — prefers over-classifying to missing a destination.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class EgressTarget:
    kind: str    # url | git_remote | s3_bucket | gcs_bucket | scp_target |
                 # ssh_target | docker_registry | package_publish
    target: str


_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("url",             re.compile(r"https?://[^\s'\">]+")),
    ("git_remote",      re.compile(r"git\s+(?:clone|push|fetch|pull|remote\s+add)\s+\S+")),
    ("git_remote",      re.compile(r"(?:git@|ssh://git)[^\s'\">]+")),
    ("s3_bucket",       re.compile(r"s3://[^\s'\">]+")),
    ("gcs_bucket",      re.compile(r"gs://[^\s'\">]+")),
    ("scp_target",      re.compile(r"scp\s+\S+\s+\S+:\S+")),
    ("ssh_target",      re.compile(r"\bssh\s+(?:-\w+\s+)*[\w.@-]+@[\w.-]+")),
    ("docker_registry", re.compile(r"docker\s+(?:push|pull|login)\s+\S+")),
    ("package_publish", re.compile(r"(?:npm\s+publish|twine\s+upload|cargo\s+publish|pip\s+upload)\b")),
]


def classify_egress(command: str) -> list[EgressTarget]:
    """Return every network destination the command would reach (deduped by (kind, target))."""
    seen: set[tuple[str, str]] = set()
    out: list[EgressTarget] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(command or ""):
            tgt = m.group()[:120]
            if (kind, tgt) not in seen:
                seen.add((kind, tgt))
                out.append(EgressTarget(kind=kind, target=tgt))
    return out
