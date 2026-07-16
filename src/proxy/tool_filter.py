"""
Relevance-based tool filtering for weak models.

A Claude Code / opencode client with many MCP servers connected sends the FULL tool
catalog on every request — commonly 300-400 tools. DeepSeek-V3 cannot reliably pick the
right tool from hundreds of options, and the raw catalog (400KB+) drowns the actual task,
so the model ignores tools entirely and answers in prose.

Real agents solve this with "tool search": only expose tools plausibly relevant to the
current turn. This module does the pragmatic version — always keep the small set of core
(non-MCP) tools, and include an MCP server's tools only when the conversation text
references that server or one of its tool names. The client still validates that the model
only calls real tools, and every tool we emit IS a real tool, so a filtered call is valid;
the only risk is dropping a tool the task needs, which we mitigate by keeping cores always
and by keying MCP inclusion on explicit mention.
"""
import re
from typing import Any, Dict, List

Tool = Dict[str, Any]


def _tool_name(t: Tool) -> str:
    return (t.get("function", t) or {}).get("name", "") if isinstance(t, dict) else ""


def _server_of(mcp_name: str) -> str:
    """`mcp__claude_ai_ClickUp__clickup_create_task` -> `clickup` (server keyword)."""
    # Strip the mcp__ prefix, take the server segment before the last __tool part.
    body = mcp_name[len("mcp__"):] if mcp_name.startswith("mcp__") else mcp_name
    parts = body.split("__")
    if not parts:
        return ""
    # e.g. ["claude_ai_ClickUp", "clickup_create_task"] — server is parts[0]'s last token
    server = parts[0].split("_")[-1] if parts else ""
    return server.lower()


def filter_tools(tools: List[Tool], conversation_text: str, max_tools: int = 60) -> List[Tool]:
    """
    Return a relevance-trimmed tool list.

    - All non-MCP (core) tools are always kept.
    - MCP tools are kept only if their server keyword or tool name appears in the
      conversation text (case-insensitive).
    - The result is capped at `max_tools` (cores first, then matched MCP tools).
    """
    if not tools:
        return tools

    text = (conversation_text or "").lower()
    core: List[Tool] = []
    matched_mcp: List[Tool] = []

    for t in tools:
        name = _tool_name(t)
        if not name:
            continue
        if not name.startswith("mcp__"):
            core.append(t)
            continue
        server = _server_of(name)
        # Include this MCP tool if its server keyword or a distinctive tail of its name
        # is mentioned in the conversation.
        tail = name.split("__")[-1].lower()
        if (server and server in text) or (tail and tail in text):
            matched_mcp.append(t)

    result = core + matched_mcp
    if len(result) > max_tools:
        # Keep all cores; trim MCP overflow (cores are the coding essentials).
        keep_mcp = max(0, max_tools - len(core))
        result = core + matched_mcp[:keep_mcp]
    return result


def summarize_filter(original: List[Tool], filtered: List[Tool]) -> str:
    """A short human-readable note about what got dropped (for logging)."""
    dropped = len(original) - len(filtered)
    if dropped <= 0:
        return f"tools: {len(original)} (no filtering)"
    return f"tools: {len(original)} -> {len(filtered)} (dropped {dropped} irrelevant MCP tools)"
