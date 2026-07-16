"""Unit tests for the conversation/session mapping — pure logic, no network."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.proxy.conversation import ConversationTracker


def _u(text):
    return {"role": "user", "content": text}


def _a(text):
    return {"role": "assistant", "content": text}


def _tool(cid, text):
    return {"role": "tool", "tool_call_id": cid, "content": text}


SYS = "TOOL SYSTEM PROMPT"


def test_first_request_is_new_session():
    t = ConversationTracker()
    plan = t.plan_turn([{"role": "system", "content": "sys"}, _u("hello")], SYS)
    assert plan.is_new_session is True
    assert len(plan.new_turns) == 1
    assert plan.new_turns[0]["content"] == "hello"
    assert SYS in plan.system_prompt


def test_continuation_sends_only_new_turns():
    t = ConversationTracker()
    msgs = [_u("hello")]
    plan1 = t.plan_turn(msgs, SYS)
    t.commit(plan1, ds_session_id="S1", new_parent_id="M1")

    # opencode now sends: original user + assistant reply + a new user turn.
    msgs2 = [_u("hello"), _a("hi there"), _u("now do X")]
    plan2 = t.plan_turn(msgs2, SYS)
    assert plan2.is_new_session is False
    assert plan2.prior_session_id == "S1"
    assert plan2.prior_parent_id == "M1"
    # Only the 2 appended turns should be sent, not "hello" again.
    assert [m["content"] for m in plan2.new_turns] == ["hi there", "now do X"]


def test_tool_result_continuation():
    """The agentic loop: assistant tool_call, then a tool result appended."""
    t = ConversationTracker()
    m1 = [_u("read file X")]
    p1 = t.plan_turn(m1, SYS)
    t.commit(p1, "S1", "M1")

    m2 = [
        _u("read file X"),
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}]},
        _tool("c1", "file contents here"),
    ]
    p2 = t.plan_turn(m2, SYS)
    assert p2.is_new_session is False
    assert len(p2.new_turns) == 2
    assert p2.new_turns[-1]["role"] == "tool"


def test_divergence_starts_fresh_session():
    """If history shrinks or the prefix changes, it's a new/branched conversation."""
    t = ConversationTracker()
    p1 = t.plan_turn([_u("first convo")], SYS)
    t.commit(p1, "S1", "M1")

    # A different first user turn => different conversation key => new session.
    p2 = t.plan_turn([_u("totally different convo")], SYS)
    assert p2.is_new_session is True
    assert p2.conversation_key != p1.conversation_key


def test_edited_history_same_first_turn_but_diverged_middle():
    """Same opening turn, but a middle turn changed => not an extension => fresh."""
    t = ConversationTracker()
    p1 = t.plan_turn([_u("hi"), _a("A"), _u("more")], SYS)
    t.commit(p1, "S1", "M1")

    # Same first turn "hi", but second turn differs -> not a strict extension.
    p2 = t.plan_turn([_u("hi"), _a("DIFFERENT"), _u("more")], SYS)
    assert p2.is_new_session is True


def test_commit_updates_parent_for_chaining():
    t = ConversationTracker()
    p1 = t.plan_turn([_u("x")], SYS)
    t.commit(p1, "S1", "M1")
    p2 = t.plan_turn([_u("x"), _a("y"), _u("z")], SYS)
    t.commit(p2, "S1", "M2")
    p3 = t.plan_turn([_u("x"), _a("y"), _u("z"), _a("w"), _u("q")], SYS)
    assert p3.prior_parent_id == "M2"  # chained forward
    assert p3.prior_session_id == "S1"


def test_explicit_conversation_id_keys_stably():
    """With a stable conversation_id, turns continue even if content-hash would differ."""
    t = ConversationTracker()
    p1 = t.plan_turn([_u("hi")], SYS, conversation_id="SESS-1")
    t.commit(p1, "S1", 1)
    # Same session id, extended history -> continuation on the SAME DeepSeek session.
    p2 = t.plan_turn([_u("hi"), _a("hello"), _u("next")], SYS, conversation_id="SESS-1")
    assert p2.is_new_session is False
    assert p2.prior_session_id == "S1"
    assert [m["content"] for m in p2.new_turns] == ["hello", "next"]


def test_different_conversation_id_is_new_session():
    t = ConversationTracker()
    p1 = t.plan_turn([_u("hi")], SYS, conversation_id="SESS-1")
    t.commit(p1, "S1", 1)
    p2 = t.plan_turn([_u("hi")], SYS, conversation_id="SESS-2")  # different session id
    assert p2.is_new_session is True
    assert p2.conversation_key != p1.conversation_key


def test_explicit_id_survives_content_change_that_would_break_hashing():
    """The old content-hash bug: identical history but a reformatted middle turn -> new.
    With a stable id, it stays a continuation."""
    t = ConversationTracker()
    p1 = t.plan_turn([_u("hi"), _a("A"), _u("more")], SYS, conversation_id="SESS-9")
    t.commit(p1, "S1", 1)
    # Middle assistant turn text differs slightly (Claude Code reformatting) but same session
    p2 = t.plan_turn([_u("hi"), _a("A"), _u("more"), _a("B"), _u("continue")],
                     SYS, conversation_id="SESS-9")
    assert p2.is_new_session is False
    assert p2.prior_session_id == "S1"


def test_upstream_system_folded_in():
    t = ConversationTracker()
    plan = t.plan_turn(
        [{"role": "system", "content": "UPSTREAM RULES"}, _u("hi")],
        SYS,
    )
    assert "UPSTREAM RULES" in plan.system_prompt
    assert SYS in plan.system_prompt
    # Our tool protocol must come AFTER the upstream prompt (recency = authority for a
    # weak model), with the override directive between them.
    assert plan.system_prompt.index("UPSTREAM RULES") < plan.system_prompt.index(SYS)
    assert "OVERRIDES ANY TOOL FORMAT ABOVE" in plan.system_prompt


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
