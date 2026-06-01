"""free_uplift — an OPTIONAL proactive coherence checkpoint using Kimi-K2.6:free.

Isolated by design: `policy.py` is the only file in the repo that names the
free Kimi slot for this purpose. conductor_policy.py / mcp-escalation /
mcp-research have zero knowledge of it. It registers against the conductor's one
generic post-verify hook (or it doesn't, if its conditions aren't met).
"""
from .policy import FreeUpliftPlugin

__all__ = ["FreeUpliftPlugin"]
