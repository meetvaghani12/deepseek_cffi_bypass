import logging
from flask import Flask, request, jsonify
from src.api.client import DeepSeekClient

app = Flask(__name__)
logger = logging.getLogger(__name__)

_client: DeepSeekClient | None = None


def get_client() -> DeepSeekClient:
    global _client
    if _client is None:
        _client = DeepSeekClient()
    return _client


@app.route("/")
def index():
    return INDEX_HTML


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400

    try:
        client = get_client()
        resp = client.chat(message)
        content = resp.choices[0].message.content
        return jsonify({"reply": content})
    except Exception as e:
        logger.exception("chat error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def reset():
    global _client
    if _client:
        try:
            _client.session.close()
        except Exception:
            pass
        _client = None
    return jsonify({"ok": True})


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DeepSeek Chat</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #e6edf3; height: 100vh; display: flex; flex-direction: column; }
  header { padding: 16px 24px; background: #161b22; border-bottom: 1px solid #30363d; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 18px; font-weight: 600; }
  header .badge { background: #238636; color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 10px; }
  #chat { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
  .msg { max-width: 75%; padding: 12px 16px; border-radius: 12px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; font-size: 14px; }
  .msg.user { align-self: flex-end; background: #1f6feb; color: #fff; border-bottom-right-radius: 4px; }
  .msg.bot { align-self: flex-start; background: #21262d; border: 1px solid #30363d; border-bottom-left-radius: 4px; }
  .msg.error { align-self: center; background: #3d1214; border: 1px solid #6e3630; color: #f85149; font-size: 13px; }
  .typing { align-self: flex-start; padding: 12px 16px; background: #21262d; border: 1px solid #30363d; border-radius: 12px; display: none; }
  .typing span { display: inline-block; width: 6px; height: 6px; background: #8b949e; border-radius: 50%; margin: 0 2px; animation: bounce 1.2s infinite; }
  .typing span:nth-child(2) { animation-delay: 0.2s; }
  .typing span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce { 0%,80%,100% { transform: translateY(0); } 40% { transform: translateY(-6px); } }
  #input-bar { padding: 16px 24px; background: #161b22; border-top: 1px solid #30363d; display: flex; gap: 12px; }
  #input-bar textarea { flex: 1; background: #0d1117; border: 1px solid #30363d; color: #e6edf3; border-radius: 8px; padding: 10px 14px; font-size: 14px; font-family: inherit; resize: none; outline: none; min-height: 42px; max-height: 120px; }
  #input-bar textarea:focus { border-color: #1f6feb; }
  #input-bar button { background: #238636; color: #fff; border: none; border-radius: 8px; padding: 10px 20px; font-size: 14px; font-weight: 600; cursor: pointer; }
  #input-bar button:hover { background: #2ea043; }
  #input-bar button:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
</head>
<body>
<header>
  <h1>DeepSeek Chat</h1>
  <span class="badge">curl_cffi</span>
</header>
<div id="chat">
  <div class="msg bot">Hi! I'm DeepSeek, how can I help you today?</div>
  <div class="typing" id="typing"><span></span><span></span><span></span></div>
</div>
<div id="input-bar">
  <textarea id="msg" rows="1" placeholder="Type a message..." autofocus></textarea>
  <button id="send" onclick="send()">Send</button>
</div>
<script>
const chat = document.getElementById('chat');
const msgEl = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const typing = document.getElementById('typing');

msgEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
msgEl.addEventListener('input', () => {
  msgEl.style.height = 'auto';
  msgEl.style.height = Math.min(msgEl.scrollHeight, 120) + 'px';
});

function addMsg(text, cls) {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  chat.insertBefore(div, typing);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

async function send() {
  const text = msgEl.value.trim();
  if (!text) return;
  msgEl.value = '';
  msgEl.style.height = 'auto';
  addMsg(text, 'user');
  sendBtn.disabled = true;
  typing.style.display = 'block';
  chat.scrollTop = chat.scrollHeight;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    typing.style.display = 'none';
    if (data.error) {
      addMsg('Error: ' + data.error, 'error');
    } else {
      addMsg(data.reply, 'bot');
    }
  } catch (err) {
    typing.style.display = 'none';
    addMsg('Network error: ' + err.message, 'error');
  }
  sendBtn.disabled = false;
  msgEl.focus();
}
</script>
</body>
</html>"""
