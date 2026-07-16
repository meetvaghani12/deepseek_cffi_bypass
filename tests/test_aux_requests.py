"""Tests for Claude Code auxiliary-request detection + short-circuiting."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.proxy.aux_requests import classify_and_answer


def _u(text):
    return {"role": "user", "content": text}


def test_suggestion_mode_returns_empty():
    sys_text = "[SUGGESTION MODE: Suggest what the user might naturally type next into Claude Code.]"
    out = classify_and_answer([_u("read this file")], sys_text)
    assert out == ""  # empty = no suggestion shown


def test_title_generation_returns_json_title():
    sys_text = ('Generate a concise, sentence-case title (3-7 words) ... Return JSON with a '
                'single "title" field.')
    msgs = [_u("<session>\n~/proj/app.py\n\nrefactor the auth module\n</session>")]
    out = classify_and_answer(msgs, sys_text)
    obj = json.loads(out)
    assert "title" in obj
    assert "auth" in obj["title"].lower()


def test_is_new_topic_returns_json_with_flag():
    sys_text = "Analyze if this message indicates a new conversation topic. Format as JSON with 'title'."
    out = classify_and_answer([_u("start a new feature")], sys_text)
    obj = json.loads(out)
    assert obj["isNewTopic"] is True and "title" in obj


def test_summary_under_50_chars():
    sys_text = "Summarize this coding conversation in under 50 characters."
    out = classify_and_answer([_u("fix the SSE parser bug in client.py")], sys_text)
    assert 0 < len(out) <= 50


def test_bash_safety_canned():
    sys_text = "Your task is to process Bash commands that an AI coding agent wants to run."
    out = classify_and_answer([_u("rm -rf /")], sys_text)
    assert "none" in out


def test_filepath_extraction_empty():
    sys_text = "Extract any file paths that this command reads or modifies."
    out = classify_and_answer([_u("cat foo.py")], sys_text)
    assert "filepaths" in out


def test_quota_probe():
    assert classify_and_answer([_u("quota")], "") == "ok"


def test_warmup_count_probe():
    assert classify_and_answer([_u("count")], "") == "ok"


def test_real_coding_request_not_short_circuited():
    """A genuine coding request must NOT be short-circuited (returns None -> goes to DeepSeek)."""
    sys_text = "You are Claude Code, Anthropic's official CLI for Claude."
    out = classify_and_answer([_u("read src/app.py and fix the bug")], sys_text)
    assert out is None


def test_end_to_end_short_circuit_no_deepseek_call():
    """A suggestion request must not create a DeepSeek session or call chat()."""
    import scripts.proxy_server as proxy
    from src.proxy.conversation import ConversationTracker

    class NoCallClient:
        created = 0
        chatted = 0
        def create_session(self): NoCallClient.created += 1; return "S1"
        def chat(self, *a, **k): NoCallClient.chatted += 1; raise AssertionError("must not call DeepSeek")
    proxy.get_client = lambda: NoCallClient()
    proxy._client = NoCallClient()
    proxy._tracker = ConversationTracker()

    msgs = [{"role": "system", "content": "[SUGGESTION MODE: Suggest what the user might type next]"},
            {"role": "user", "content": "read a file"}]
    text, calls = proxy.run_turn(msgs, None, model="claude-3-5-haiku-20241022")
    assert text == "" and calls == []
    assert NoCallClient.created == 0 and NoCallClient.chatted == 0


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try: t(); print(f"  PASS  {t.__name__}"); passed += 1
        except Exception: print(f"  FAIL  {t.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
