"""Tests for relevance-based tool filtering (handles Claude Code's 300-400 tool catalog)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.proxy.tool_filter import filter_tools, summarize_filter


def _t(name):
    return {"type": "function", "function": {"name": name, "description": "d",
            "parameters": {"type": "object", "properties": {}}}}


CORE = [_t("Read"), _t("Write"), _t("Edit"), _t("Bash"), _t("Glob"), _t("Grep")]
MCP = [
    _t("mcp__claude_ai_ClickUp__clickup_create_task"),
    _t("mcp__claude_ai_ClickUp__clickup_delete_task"),
    _t("mcp__claude_ai_bioRxiv__search_preprints"),
    _t("mcp__claude_ai_ChEMBL__drug_search"),
]
ALL = CORE + MCP


def test_core_tools_always_kept():
    out = filter_tools(ALL, "read the file /etc/hostname")
    names = [t["function"]["name"] for t in out]
    for c in ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]:
        assert c in names
    # No MCP mentioned -> all 360-style MCP dropped
    assert not any(n.startswith("mcp__") for n in names)


def test_mcp_included_when_server_mentioned():
    out = filter_tools(ALL, "create a clickup task for this bug")
    names = [t["function"]["name"] for t in out]
    assert "mcp__claude_ai_ClickUp__clickup_create_task" in names
    assert "mcp__claude_ai_ClickUp__clickup_delete_task" in names
    # unrelated servers still dropped
    assert "mcp__claude_ai_bioRxiv__search_preprints" not in names


def test_no_mcp_mention_drops_all_mcp():
    out = filter_tools(ALL, "refactor the auth module and run the tests")
    assert all(not t["function"]["name"].startswith("mcp__") for t in out)
    assert len(out) == len(CORE)


def test_cap_trims_mcp_not_core():
    # 5 cores + 100 matching mcp tools, cap 10 -> keep 5 core + 5 mcp
    many = [_t(f"mcp__claude_ai_ClickUp__clickup_op_{i}") for i in range(100)]
    out = filter_tools(CORE + many, "clickup", max_tools=10)
    names = [t["function"]["name"] for t in out]
    assert all(c in names for c in ["Read", "Write", "Edit", "Bash", "Glob", "Grep"])
    assert len(out) == 10


def test_empty_tools_passthrough():
    assert filter_tools([], "anything") == []
    assert filter_tools(None, "anything") is None or filter_tools(None, "anything") == []


def test_massive_catalog_shrinks_dramatically():
    # Simulate Claude Code: 37 core + 360 MCP, generic coding request
    core = [_t(f"Core{i}") for i in range(37)]
    mcp = [_t(f"mcp__claude_ai_Srv{i}__op") for i in range(360)]
    out = filter_tools(core + mcp, "read config.py and summarize it")
    assert len(out) == 37, f"expected only core tools, got {len(out)}"
    print(summarize_filter(core + mcp, out))


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try: t(); print(f"  PASS  {t.__name__}"); passed += 1
        except Exception: print(f"  FAIL  {t.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
