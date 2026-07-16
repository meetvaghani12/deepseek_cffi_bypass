"""
Integration test for the proxy translation flow with a MOCK DeepSeek client.
Proves request -> plan -> render -> DeepSeek(text) -> parse -> OpenAI output end to end,
including the multi-turn agentic loop, without any browser/network.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.proxy_server as proxy
from src.api.models import ChatResponse, Choice, ChatMessage


class MockClient:
    """Scripted DeepSeek client: returns queued replies, records prompts it received."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts = []
        self.sessions_created = 0
        self._mid = 0

    def create_session(self):
        self.sessions_created += 1
        return f"S{self.sessions_created}"

    def chat(self, prompt, chat_session_id=None, parent_message_id=None, **kw):
        self.prompts.append({"prompt": prompt, "session": chat_session_id,
                             "parent": parent_message_id})
        self._mid += 1
        reply = self._replies.pop(0) if self._replies else ""
        # DeepSeek returns message_id as an INTEGER; the proxy must chain that native
        # type as parent_message_id (a string 422s the real API).
        return ChatResponse(id=str(self._mid), message_id=self._mid,
                            choices=[Choice(message=ChatMessage(role="assistant", content=reply))])


TOOLS = [
    {"type": "function", "function": {
        "name": "read", "description": "Read a file.",
        "parameters": {"type": "object",
                       "properties": {"filePath": {"type": "string"}},
                       "required": ["filePath"]}}},
]


def _install_mock(replies):
    mock = MockClient(replies)
    proxy._client = mock
    proxy._client_lock  # exists
    # Fresh tracker each test to avoid cross-test state.
    from src.proxy.conversation import ConversationTracker
    proxy._tracker = ConversationTracker()
    proxy.get_client = lambda: mock
    return mock


def test_plain_text_turn():
    mock = _install_mock(["Hello! How can I help?"])
    text, calls = proxy.run_turn([{"role": "user", "content": "hi"}], None)
    assert calls == []
    assert text == "Hello! How can I help?"
    assert mock.sessions_created == 1


def test_tool_call_emitted_as_openai():
    mock = _install_mock(["<thinking>need to read</thinking><read><filePath>a.py</filePath></read>"])
    text, calls = proxy.run_turn([{"role": "user", "content": "read a.py"}], TOOLS)
    assert text == ""
    assert len(calls) == 1
    assert calls[0].name == "read"
    assert calls[0].arguments == {"filePath": "a.py"}
    # The DeepSeek prompt must have included the tool-teaching system prompt on new session.
    assert "## read" in mock.prompts[0]["prompt"]


def test_agentic_loop_continuation_sends_only_result():
    """
    Simulate opencode's loop:
      req1: user asks -> DeepSeek emits tool call
      req2: full history + tool result -> DeepSeek emits final text
    The 2nd DeepSeek prompt must contain ONLY the tool result (not the whole history),
    and must reuse the same session with chained parent.
    """
    mock = _install_mock([
        "<read><filePath>a.py</filePath></read>",       # turn 1: tool call
        "The file prints hello.",                          # turn 2: final answer
    ])

    # Request 1
    msgs1 = [{"role": "user", "content": "what does a.py do?"}]
    text1, calls1 = proxy.run_turn(msgs1, TOOLS)
    assert len(calls1) == 1 and text1 == ""

    # Request 2: opencode appends the assistant tool_call + the tool result.
    msgs2 = [
        {"role": "user", "content": "what does a.py do?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read", "arguments": json.dumps({"filePath": "a.py"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "print('hello')"},
    ]
    text2, calls2 = proxy.run_turn(msgs2, TOOLS)
    assert calls2 == []
    assert text2 == "The file prints hello."

    # Session reuse + parent chaining. parent MUST be the native int message_id (1),
    # not a string — DeepSeek's API validates parent_message_id as a u32.
    assert mock.sessions_created == 1, "should reuse one DeepSeek session"
    assert mock.prompts[1]["session"] == "S1"
    assert mock.prompts[1]["parent"] == 1, "second call must chain native int parent_message_id"
    assert isinstance(mock.prompts[1]["parent"], int), "parent must be int, not str (422 otherwise)"

    # KV-cache win: the 2nd prompt must NOT resend the original question or system prompt.
    p2 = mock.prompts[1]["prompt"]
    assert "what does a.py do?" not in p2
    assert "## read" not in p2
    assert "print('hello')" in p2
    assert p2.startswith("[read"), f"tool result should be labeled: {p2[:40]!r}"


def test_fresh_session_with_tool_result_replays_call_and_answers():
    """
    Regression: a request that arrives fresh (proxy restart / untracked convo) but already
    contains an assistant tool_call + tool result must replay the call as XML so DeepSeek
    sees call-before-result and ANSWERS, rather than re-issuing the same tool call.
    """
    mock = _install_mock(["The file contains: my-host.local"])
    msgs = [
        {"role": "user", "content": "what is in /etc/hostname?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read", "arguments": json.dumps({"filePath": "/etc/hostname"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "my-host.local"},
    ]
    text, calls = proxy.run_turn(msgs, TOOLS)
    # The prompt DeepSeek received must contain the replayed XML call before the result.
    prompt = mock.prompts[0]["prompt"]
    assert "<read><filePath>/etc/hostname</filePath></read>" in prompt
    assert prompt.index("<read>") < prompt.index("[read for")
    # And the mock's plain-text answer should pass through (not a re-issued tool call).
    assert calls == []
    assert text == "The file contains: my-host.local"


def test_intent_narration_detector():
    from scripts.proxy_server import _looks_like_tool_intent
    assert _looks_like_tool_intent("I'll count the files. Let me check the repo.")
    assert _looks_like_tool_intent("Let me check the current state of the repo.")
    assert _looks_like_tool_intent("Sure, I'll read that file now.")
    assert _looks_like_tool_intent("Here are the steps:")
    # Real answers are NOT flagged
    assert not _looks_like_tool_intent("The repo has 42 files across 6 directories.")
    assert not _looks_like_tool_intent("This file is a test suite for the SSE parser.")


def test_reprompt_recovers_tool_call_after_narration():
    """Turn 1 narrates ('I'll check...'); the re-prompt must recover a real tool call."""
    mock = _install_mock([
        "I'll count the files. Let me check the current state of the repo.",  # narration
        "<bash><command>find . -type f | wc -l</command></bash>",             # re-prompt -> call
    ])
    text, calls = proxy.run_turn([{"role": "user", "content": "how many files in this repo?"}], TOOLS + [
        {"type": "function", "function": {"name": "bash", "description": "run",
         "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
    ])
    assert calls and calls[0].name == "bash", f"re-prompt should recover a tool call; got text={text!r}"
    assert "wc -l" in calls[0].arguments["command"]
    # both DeepSeek calls happened on the SAME session
    assert mock.prompts[1]["session"] == mock.prompts[0]["session"]


def test_no_reprompt_for_genuine_text_answer():
    """A real text answer must NOT trigger a re-prompt (only one DeepSeek call)."""
    mock = _install_mock(["The repository contains 42 files."])
    text, calls = proxy.run_turn([{"role": "user", "content": "how many files?"}], TOOLS)
    assert calls == [] and "42" in text
    assert len(mock.prompts) == 1, "genuine answer must not re-prompt"


def test_new_conversation_starts_new_session():
    mock = _install_mock(["answer one", "answer two"])
    proxy.run_turn([{"role": "user", "content": "convo A"}], None)
    proxy.run_turn([{"role": "user", "content": "convo B different"}], None)
    assert mock.sessions_created == 2


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
