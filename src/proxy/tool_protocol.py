"""
Prompt-based tool-calling protocol for DeepSeek-V3 (no native function calling).

opencode (via the Vercel AI SDK) does NATIVE function calling: it sends OpenAI-style
`tools` and expects `tool_calls` back, and it runs the agentic loop and executes tools
itself. The web DeepSeek model can only take text and return text. So the proxy's job
is TRANSLATION, not agency:

  1. Turn opencode's OpenAI `tools` schema into an XML tool-teaching system prompt that
     DeepSeek can follow  ->  build_tool_system_prompt()
  2. Parse XML tool calls out of DeepSeek's text reply                 ->  parse_tool_calls()
  3. Convert a parsed call into OpenAI `tool_calls` shape for opencode ->  to_openai_tool_call()
  4. Format opencode's returned tool results as a DeepSeek user turn   ->  format_tool_result()

XML tags (Cline/Roo-Code style) are used rather than JSON because they degrade far more
gracefully for a mid-tier model: each parameter is independently delimited, file content
between <content>...</content> needs no escaping, and partial/prose-wrapped output can be
located rather than requiring byte-perfect JSON.
"""
import re
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any]


# ---------------------------------------------------------------------------
# 1. System prompt generation
# ---------------------------------------------------------------------------

_PREAMBLE = """\
You are a coding agent operating through a tool-use protocol. You act by emitting tool
calls as XML; the environment runs each tool and returns its result in the next message.

# Tool-call format
Emit a tool call as XML. The tool name is the outer tag; each parameter is its own inner
tag. Emit EXACTLY ONE tool call per message and write nothing after the closing tag. Do
NOT predict, invent, or write a tool's result yourself — wait for it in the next message.

Example:
<read><filePath>src/main.py</filePath></read>

For a parameter whose value is an object or array, put JSON inside the tag:
<some_tool><options>{"recursive": true}</options></some_tool>

# Available tools
"""

_GUIDELINES = """\

# Guidelines
1. Think first in a <thinking>...</thinking> block: pick the single best tool and confirm
   you have every REQUIRED parameter. If a required parameter is missing and cannot be
   inferred, ask the user for it in plain text instead of calling the tool.
2. Use exactly one tool per message. Never assume a tool's outcome — each step must be
   based on the actual result of the previous step.
3. After emitting a tool call, stop and wait for the result before continuing.
4. When the task is fully complete, reply with a normal text message (no tool call)
   summarizing what you did. A plain text reply with no tool call ends your turn.
"""


def _describe_params(schema: Dict[str, Any]) -> List[str]:
    """Render a JSON-Schema parameter object into human-readable lines."""
    props = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])
    lines: List[str] = []
    for pname, pdef in props.items():
        pdef = pdef or {}
        ptype = pdef.get("type", "any")
        req = "required" if pname in required else "optional"
        desc = (pdef.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 200:
            desc = desc[:200] + "…"
        suffix = f" — {desc}" if desc else ""
        lines.append(f"  - {pname} ({ptype}, {req}){suffix}")
    return lines


def build_tool_system_prompt(tools: List[Dict[str, Any]]) -> str:
    """Build the XML tool-teaching system prompt from OpenAI-format tool defs."""
    if not tools:
        return ""
    blocks = [_PREAMBLE]
    for t in tools:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = fn.get("name", "")
        if not name:
            continue
        # Cap the tool description. Client tool descriptions (esp. Claude Code's) run to
        # multiple paragraphs of client-specific prose; the model only needs a short
        # summary of what the tool does, and the bulk drowns out the XML protocol.
        desc = (fn.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 300:
            desc = desc[:300].rsplit(" ", 1)[0] + "…"
        params = fn.get("parameters", {}) or {}
        param_lines = _describe_params(params)

        blocks.append(f"## {name}")
        if desc:
            blocks.append(desc)
        if param_lines:
            blocks.append("Parameters:")
            blocks.extend(param_lines)
        # Usage skeleton showing the exact tags to emit.
        prop_names = list((params.get("properties", {}) or {}).keys())
        inner = "".join(f"<{p}>...</{p}>" for p in prop_names) or ""
        blocks.append(f"Usage: <{name}>{inner}</{name}>")
        blocks.append("")  # blank line between tools

    blocks.append(_GUIDELINES)
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# 2. Parsing DeepSeek's XML tool calls
# ---------------------------------------------------------------------------

_THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Remove <thinking>...</thinking> blocks (kept internal, never shown to opencode)."""
    return _THINKING_RE.sub("", text or "")


def _find_tool_blocks(text: str, tool_names: List[str]) -> List[tuple]:
    """
    Locate top-level <tool>...</tool> blocks for known tool names, scanning THROUGH prose
    and markdown fences. Whitespace/case tolerant on the tag name. Returns
    [(start_index, name, inner_text), ...] in document order.
    """
    blocks = []
    for name in tool_names:
        # <name ...> ... </name>  (tolerant of surrounding whitespace and case)
        open_re = re.compile(rf"<\s*{re.escape(name)}\s*>", re.IGNORECASE)
        for m in open_re.finditer(text):
            close_re = re.compile(rf"</\s*{re.escape(name)}\s*>", re.IGNORECASE)
            # Use the LAST matching close tag after this open, so nested identical tags
            # inside a content param don't truncate early.
            closes = list(close_re.finditer(text, m.end()))
            if not closes:
                continue
            close = closes[-1]
            inner = text[m.end():close.start()]
            blocks.append((m.start(), name, inner))
    blocks.sort(key=lambda b: b[0])
    return blocks


def _coerce_value(raw: str) -> Any:
    """Turn a param string into a bool/number/object/array when it clearly is one."""
    s = raw.strip()
    if s == "":
        return ""
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    # JSON object/array
    if (s[0] == "{" and s[-1] == "}") or (s[0] == "[" and s[-1] == "]"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return raw  # keep the raw text; a schema-typed consumer may still use it
    # Integer / float
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            return s
    if re.fullmatch(r"-?\d*\.\d+", s):
        try:
            return float(s)
        except ValueError:
            return s
    return raw  # preserve original text (do not strip meaningful leading/trailing ws)


def _parse_params(inner: str) -> Dict[str, Any]:
    """
    Extract <param>value</param> pairs from a tool block's inner text. For any param, if
    it repeats, the LAST occurrence wins; content is taken between the first open and the
    LAST matching close so embedded tags survive.
    """
    params: Dict[str, Any] = {}
    # Find each distinct param tag name present.
    for pm in re.finditer(r"<\s*([a-zA-Z_][\w\-]*)\s*>", inner):
        pname = pm.group(1)
        open_re = re.compile(rf"<\s*{re.escape(pname)}\s*>", re.IGNORECASE)
        close_re = re.compile(rf"</\s*{re.escape(pname)}\s*>", re.IGNORECASE)
        first_open = open_re.search(inner)
        closes = list(close_re.finditer(inner))
        if not first_open or not closes:
            continue
        value = inner[first_open.end():closes[-1].start()]
        params[pname] = _coerce_value(value)
    return params


def parse_tool_calls(text: str, tools: List[Dict[str, Any]]) -> List[ToolCall]:
    """
    Parse tool calls out of DeepSeek's raw reply. Only tags matching a declared tool name
    are considered (so prose mentioning `<thing>` never becomes a spurious call).
    """
    tool_names = [
        (t.get("function", t) or {}).get("name")
        for t in (tools or [])
    ]
    tool_names = [n for n in tool_names if n]
    if not tool_names:
        return []

    cleaned = strip_thinking(text)
    calls: List[ToolCall] = []
    for _start, name, inner in _find_tool_blocks(cleaned, tool_names):
        calls.append(ToolCall(name=name, arguments=_parse_params(inner)))
    if calls:
        return calls

    # Fallback: DeepSeek (primed by Claude Code's own prompt) sometimes ignores the XML
    # format and emits a native function-call syntax like `Read({"file_path": "..."})` or
    # `bash({"command":"ls"})`. Accept that too rather than dropping the call.
    return _parse_json_calls(cleaned, tool_names)


def _parse_json_calls(text: str, tool_names: List[str]) -> List[ToolCall]:
    """Parse `ToolName({...json...})` or `ToolName(...json...)` style calls."""
    name_set = {n.lower(): n for n in tool_names}
    calls: List[ToolCall] = []
    # Match a known tool name followed by ( ... ) — capture the balanced-ish JSON inside.
    for m in re.finditer(r"\b([A-Za-z_][\w-]*)\s*\(\s*(\{.*?\})\s*\)", text, re.DOTALL):
        fn_name = m.group(1)
        canonical = name_set.get(fn_name.lower())
        if not canonical:
            continue
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            continue
        if isinstance(args, dict):
            calls.append(ToolCall(name=canonical, arguments=args))
    return calls


# ---------------------------------------------------------------------------
# 3 & 4. OpenAI translation + result formatting
# ---------------------------------------------------------------------------

def to_openai_tool_call(call: ToolCall, call_id: str) -> Dict[str, Any]:
    """Shape a parsed ToolCall as an OpenAI `tool_calls` array element."""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        },
    }


def format_tool_result(tool_name: str, arg_hint: str, result: str) -> str:
    """
    Format an executed tool result as the DeepSeek user turn, using Cline's
    `[tool for 'arg'] Result:` convention which reads clearly to the model.
    """
    hint = f" for '{arg_hint}'" if arg_hint else ""
    return f"[{tool_name}{hint}] Result:\n{result}"


NO_TOOL_REMINDER = (
    "[system] Your previous message did not contain a valid tool call and the task is not "
    "yet complete. Reply with exactly one tool call in the required XML format, or, if the "
    "task is finished, a plain text summary with no tool call."
)
