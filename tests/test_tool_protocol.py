"""Unit tests for the prompt-based tool-calling protocol — pure logic, no network."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.proxy.tool_protocol import (
    build_tool_system_prompt,
    parse_tool_calls,
    to_openai_tool_call,
    format_tool_result,
    strip_thinking,
)

TOOLS = [
    {"type": "function", "function": {
        "name": "read",
        "description": "Read a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to read"},
                "offset": {"type": "integer", "description": "Start line"},
            },
            "required": ["filePath"],
        },
    }},
    {"type": "function", "function": {
        "name": "write",
        "description": "Write a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "filePath": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filePath", "content"],
        },
    }},
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a command.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }},
]


def test_system_prompt_contains_tools_and_rules():
    p = build_tool_system_prompt(TOOLS)
    assert "## read" in p and "## write" in p and "## bash" in p
    assert "filePath" in p and "content" in p
    assert "one tool per message" in p.lower()
    assert "<thinking>" in p
    assert "Usage: <read>" in p


def test_empty_tools_yields_empty_prompt():
    assert build_tool_system_prompt([]) == ""


def test_parse_simple_call():
    text = "<read><filePath>src/app.py</filePath></read>"
    calls = parse_tool_calls(text, TOOLS)
    assert len(calls) == 1
    assert calls[0].name == "read"
    assert calls[0].arguments == {"filePath": "src/app.py"}


def test_parse_through_prose_and_fences():
    text = (
        "Sure, I'll read that file now.\n"
        "```xml\n"
        "<read><filePath>a/b.txt</filePath></read>\n"
        "```\n"
        "That should do it."
    )
    calls = parse_tool_calls(text, TOOLS)
    assert len(calls) == 1
    assert calls[0].arguments["filePath"] == "a/b.txt"


def test_thinking_block_stripped_and_ignored():
    text = (
        "<thinking>I should read then write. I'll start with read.</thinking>\n"
        "<read><filePath>x.py</filePath></read>"
    )
    assert "<read>" in strip_thinking(text)
    assert "thinking" not in strip_thinking(text).lower()
    calls = parse_tool_calls(text, TOOLS)
    assert len(calls) == 1 and calls[0].name == "read"


def test_content_with_embedded_xml_survives():
    """A write whose content itself contains tags must not truncate at the inner tag."""
    content = "<html>\n<body>hi</body>\n</html>"
    text = f"<write><filePath>index.html</filePath><content>{content}</content></write>"
    calls = parse_tool_calls(text, TOOLS)
    assert len(calls) == 1
    assert calls[0].arguments["filePath"] == "index.html"
    assert calls[0].arguments["content"] == content, repr(calls[0].arguments["content"])


def test_integer_param_coerced():
    text = "<read><filePath>f</filePath><offset>10</offset></read>"
    calls = parse_tool_calls(text, TOOLS)
    assert calls[0].arguments["offset"] == 10
    assert isinstance(calls[0].arguments["offset"], int)


def test_json_object_param_coerced():
    tools = [{"type": "function", "function": {
        "name": "cfg", "parameters": {"type": "object",
        "properties": {"opts": {"type": "object"}}}}}]
    text = '<cfg><opts>{"recursive": true, "n": 3}</opts></cfg>'
    calls = parse_tool_calls(text, tools)
    assert calls[0].arguments["opts"] == {"recursive": True, "n": 3}


def test_case_and_whitespace_tolerant():
    text = "<  read >< filePath >y.py</ filePath ></ read >"
    calls = parse_tool_calls(text, TOOLS)
    assert len(calls) == 1
    assert calls[0].arguments["filePath"] == "y.py"


def test_unknown_tag_not_parsed_as_call():
    text = "I love the <blink> tag and <notatool>stuff</notatool>."
    calls = parse_tool_calls(text, TOOLS)
    assert calls == []


def test_two_calls_in_order():
    text = "<read><filePath>a</filePath></read> then <bash><command>ls</command></bash>"
    calls = parse_tool_calls(text, TOOLS)
    assert [c.name for c in calls] == ["read", "bash"]


def test_json_call_fallback_read():
    """DeepSeek sometimes emits native `Read({...})` instead of XML — parse it anyway."""
    text = 'Read({"filePath": "/etc/hostname"})'
    calls = parse_tool_calls(text, TOOLS)
    assert len(calls) == 1
    assert calls[0].name == "read"  # canonicalized to the declared name
    assert calls[0].arguments == {"filePath": "/etc/hostname"}


def test_json_call_fallback_bash_with_prose():
    text = 'I will list files. bash({"command": "ls -la"})'
    calls = parse_tool_calls(text, TOOLS)
    assert len(calls) == 1 and calls[0].name == "bash"
    assert calls[0].arguments == {"command": "ls -la"}


def test_xml_preferred_over_json_when_both_present():
    text = '<read><filePath>a.py</filePath></read>'
    calls = parse_tool_calls(text, TOOLS)
    assert len(calls) == 1 and calls[0].arguments == {"filePath": "a.py"}


def test_json_call_unknown_name_ignored():
    text = 'someRandomFunc({"x": 1})'
    assert parse_tool_calls(text, TOOLS) == []


def test_to_openai_shape():
    from src.proxy.tool_protocol import ToolCall
    tc = ToolCall(name="bash", arguments={"command": "ls -la"})
    oai = to_openai_tool_call(tc, "call_123")
    assert oai["id"] == "call_123"
    assert oai["type"] == "function"
    assert oai["function"]["name"] == "bash"
    assert json.loads(oai["function"]["arguments"]) == {"command": "ls -la"}


def test_format_tool_result():
    out = format_tool_result("read", "src/app.py", "1 | print('hi')")
    assert out.startswith("[read for 'src/app.py'] Result:")
    assert "print('hi')" in out


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t(); print(f"  PASS  {t.__name__}"); passed += 1
        except Exception:
            print(f"  FAIL  {t.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
