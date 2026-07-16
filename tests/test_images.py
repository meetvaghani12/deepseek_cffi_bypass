"""Tests for image/PDF attachment handling: extraction, upload wiring, vision routing."""
import sys
import os
import base64

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.proxy import anthropic_api

_B64 = base64.b64encode(b"fake-image-bytes").decode()


def _img_block(mt="image/png"):
    return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": _B64}}


def _pdf_block():
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": _B64}}


# ---- extraction ----

def test_extract_image_block():
    atts = anthropic_api._extract_attachments([_img_block(), {"type": "text", "text": "what's this?"}])
    assert len(atts) == 1
    assert atts[0]["kind"] == "image"
    assert atts[0]["media_type"] == "image/png"
    assert atts[0]["data"] == _B64
    assert atts[0]["filename"].endswith(".png")


def test_extract_pdf_block():
    atts = anthropic_api._extract_attachments([_pdf_block()])
    assert atts[0]["kind"] == "document"
    assert atts[0]["filename"].endswith(".pdf")


def test_extract_skips_url_source():
    block = {"type": "image", "source": {"type": "url", "url": "http://x/y.png"}}
    assert anthropic_api._extract_attachments([block]) == []


def test_anthropic_to_internal_preserves_attachments():
    data = {"messages": [{"role": "user", "content": [_img_block(), {"type": "text", "text": "describe"}]}]}
    messages, _ = anthropic_api.anthropic_to_internal(data)
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "describe"
    assert "attachments" in messages[0]
    assert messages[0]["attachments"][0]["kind"] == "image"


# ---- end-to-end upload wiring ----

def test_run_turn_uploads_image_and_routes_vision():
    import scripts.proxy_server as proxy
    from src.api.models import ChatResponse, Choice, ChatMessage
    from src.proxy.conversation import ConversationTracker

    captured = {}

    class MockSession:
        def upload_image(self, b64, media_type="image/png", filename="x", vision=True):
            captured["uploaded"] = {"vision": vision, "media_type": media_type}
            return {"file_id": "vfile_123", "vision": vision, "status": "SUCCESS"}
        def get_hif_headers(self):
            return {"x-hif-leim": "L", "x-hif-dliq": "D"}

    class MockClient:
        session = MockSession()
        def create_session(self): return "S1"
        def chat(self, prompt, chat_session_id=None, parent_message_id=None,
                 ref_file_ids=None, model_type="default", extra_headers=None, **kw):
            captured["chat"] = {"ref_file_ids": ref_file_ids, "model_type": model_type,
                                "extra_headers": extra_headers}
            return ChatResponse(id="1", message_id=1,
                choices=[Choice(message=ChatMessage(role="assistant", content="A red apple on a table."))])

    proxy.get_client = lambda: MockClient()
    proxy._client = MockClient()
    proxy._tracker = ConversationTracker()

    messages = [{"role": "user", "content": "what is in this image?",
                 "attachments": [{"kind": "image", "media_type": "image/png",
                                  "data": _B64, "filename": "photo.png"}]}]
    text, calls = proxy.run_turn(messages, None)

    # uploaded as vision, forked
    assert captured["uploaded"]["vision"] is True
    # completion routed to vision model with the ref file id + HIF headers
    assert captured["chat"]["ref_file_ids"] == ["vfile_123"]
    assert captured["chat"]["model_type"] == "vision"
    assert captured["chat"]["extra_headers"] == {"x-hif-leim": "L", "x-hif-dliq": "D"}
    assert "apple" in text


def test_run_turn_pdf_uses_ocr_not_vision():
    import scripts.proxy_server as proxy
    from src.api.models import ChatResponse, Choice, ChatMessage
    from src.proxy.conversation import ConversationTracker

    captured = {}

    class MockSession:
        def upload_image(self, b64, media_type="image/png", filename="x", vision=True):
            captured["vision_arg"] = vision
            return {"file_id": "doc_9", "vision": False, "status": "SUCCESS"}  # PDF -> no vision
        def get_hif_headers(self): return {}

    class MockClient:
        session = MockSession()
        def create_session(self): return "S1"
        def chat(self, prompt, ref_file_ids=None, model_type="default", extra_headers=None, **kw):
            captured["model_type"] = model_type
            captured["ref"] = ref_file_ids
            return ChatResponse(id="1", message_id=1,
                choices=[Choice(message=ChatMessage(role="assistant", content="This PDF is an invoice."))])

    proxy.get_client = lambda: MockClient()
    proxy._client = MockClient()
    proxy._tracker = ConversationTracker()

    messages = [{"role": "user", "content": "summarize this pdf",
                 "attachments": [{"kind": "document", "media_type": "application/pdf",
                                  "data": _B64, "filename": "doc.pdf"}]}]
    proxy.run_turn(messages, None)
    assert captured["vision_arg"] is False   # documents don't fork to vision
    assert captured["model_type"] == "default"
    assert captured["ref"] == ["doc_9"]


def test_run_turn_upload_failure_falls_back_to_text():
    import scripts.proxy_server as proxy
    from src.api.models import ChatResponse, Choice, ChatMessage
    from src.proxy.conversation import ConversationTracker

    class MockSession:
        def upload_image(self, *a, **k): return {"error": "file parse timeout"}
        def get_hif_headers(self): return {}

    class MockClient:
        session = MockSession()
        def create_session(self): return "S1"
        def chat(self, prompt, ref_file_ids=None, model_type="default", **kw):
            assert ref_file_ids is None, "failed upload must not pass a ref id"
            return ChatResponse(id="1", message_id=1,
                choices=[Choice(message=ChatMessage(role="assistant", content="I couldn't see an image."))])

    proxy.get_client = lambda: MockClient()
    proxy._client = MockClient()
    proxy._tracker = ConversationTracker()

    messages = [{"role": "user", "content": "what is this?",
                 "attachments": [{"kind": "image", "media_type": "image/png", "data": _B64, "filename": "x.png"}]}]
    text, calls = proxy.run_turn(messages, None)
    assert text  # still returns a text answer, no crash


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try: t(); print(f"  PASS  {t.__name__}"); passed += 1
        except Exception: print(f"  FAIL  {t.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
