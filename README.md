<div align="center">

# 🔐 DeepSeek PoW Bypass

### Automated DeepSeek Chat Client with PoW Challenge Solving

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-1.45+-green?style=for-the-badge&logo=playwright&logoColor=white)
![curl_cffi](https://img.shields.io/badge/curl__cffi-TLS%20Impersonation-orange?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

<br/>

```
 ██████╗ ███████╗███╗   ██╗████████╗███████╗███████╗
██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝██╔════╝██╔════╝
██║  ███╗█████╗  ██╔██╗ ██║   ██║   █████╗  ███████╗
██║   ██║██╔══╝  ██║╚██╗██║   ██║   ██╔══╝  ╚════██║
╚██████╔╝███████╗██║ ╚████║   ██║   ███████╗███████║
 ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚══════╝
            PoW Bypass • Chat Automation
```

---

</div>

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔑 **Auto Login** | Persistent browser profile — login once, use forever |
| ⚡ **PoW Solver** | WebAssembly-based challenge solver via in-browser Worker |
| 🛡️ **TLS Impersonation** | `curl_cffi` with Chrome 99 fingerprint |
| 🌐 **Web UI** | Clean chat interface at `localhost:5050` |
| 💬 **Session Memory** | Full conversation context across messages |
| 🔄 **Auto Retry** | Fallback to CDP intercept if Worker PoW fails |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Web UI (Flask)                        │
│                   localhost:5050                         │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  DeepSeekClient                          │
│          Session management • SSE parsing                │
└──────┬───────────────────────────────────┬──────────────┘
       │                                   │
┌──────▼──────────┐              ┌─────────▼──────────────┐
│  Browser Thread │              │    curl_cffi (TLS)     │
│  Playwright     │              │    chrome99 fingerprint │
│  ├─ Login       │              │    DeepSeek API calls   │
│  ├─ PoW Worker  │              └────────────────────────┘
│  └─ CDP Intercept│
└─────────────────┘
```

---

## 🚀 Quick Start

### 1. Clone & Setup

```bash
git clone https://github.com/meetvaghani12/deepseek_cffi_bypass.git
cd deepseek_cffi_bypass

python -m venv meet312
source meet312/bin/activate   # macOS/Linux
# meet312\Scripts\activate    # Windows

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Credentials

```bash
cp .env.example .env
```

```env
DEEPSEEK_EMAIL=your_email@gmail.com
DEEPSEEK_PASSWORD=your_password
```

### 3. Run

**Web UI (recommended):**
```bash
PYTHONPATH=. python scripts/web_ui.py
# Open http://localhost:5050
```

**CLI:**
```bash
PYTHONPATH=. python scripts/interactive_demo.py
```

**Python API:**
```python
from src.api.client import DeepSeekClient

client = DeepSeekClient()
print(client.chat("Hello!").choices[0].message.content)
client.session.close()
```

---

## 📁 Project Structure

```
deepseek_pow_bypass/
├── src/
│   ├── api/
│   │   ├── client.py          # DeepSeekClient — chat + SSE parsing
│   │   └── models.py          # Pydantic response models
│   ├── browser/
│   │   ├── launcher.py        # Persistent browser context
│   │   ├── solver.py          # Automated login + CAPTCHA handling
│   │   └── context.py         # Browser context management
│   ├── network/
│   │   ├── session.py         # BrowserSession + PersistentSession
│   │   ├── pow.py             # PoW solving (Worker + CDP fallback)
│   │   └── tls.py             # curl_cffi TLS impersonation
│   ├── anti_detection/
│   │   ├── fingerprint.py     # Browser fingerprint spoofing
│   │   └── headers.py         # Realistic request headers
│   ├── cookies/
│   │   └── refresh.py         # Cookie management
│   └── web/
│       └── server.py          # Flask web UI
├── scripts/
│   ├── web_ui.py              # Launch web server
│   └── interactive_demo.py    # CLI chat
├── data/
│   └── browser_profile/       # Persistent Chromium profile
├── .env.example
├── requirements.txt
└── README.md
```

---

## ⚙️ How It Works

### 1. Authentication
```
Browser launches → Persistent profile preserves cookies
→ Auto-login if needed → userToken captured from localStorage
→ Bearer token = "Bearer " + userToken
```

### 2. PoW Challenge Solving
```
Request chat/completion
→ App intercepts via WASM Worker
→ Solves SHA-3 challenge (difficulty: 144000)
→ Attaches x-ds-pow-response header
→ curl_cffi sends request with TLS impersonation
```

### 3. Fallback: CDP Intercept
```
Worker fails → CDP Fetch intercept enabled
→ Type message in browser → Intercept chat/completion request
→ Capture auth + PoW headers → Strip PoW (preserve for reuse)
→ Forward with curl_cffi
```

---

## 🔧 API Usage

```python
from src.api.client import DeepSeekClient

client = DeepSeekClient()

# Single message
response = client.chat("Explain quantum computing")
print(response.choices[0].message.content)

# Conversation (automatically maintains context)
client.chat("My name is Alice")
response = client.chat("What's my name?")  # Remembers "Alice"

# With options
response = client.chat(
    "Search for latest news",
    thinking_enabled=True,
    search_enabled=True
)

# Reset session
client.session.close()
client = DeepSeekClient()
```

---

## 📋 Requirements

| Dependency | Version | Purpose |
|-----------|---------|---------|
| Python | 3.10+ | Runtime |
| Playwright | 1.45+ | Browser automation |
| curl_cffi | 0.5.10+ | TLS impersonation |
| Flask | 3.0+ | Web UI |
| Pydantic | 2.0+ | Data models |

---

## ⚠️ Disclaimer

This project is for **educational purposes only**. Use responsibly and respect DeepSeek's Terms of Service. The author is not responsible for any misuse.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built with 🧠 by [Meet Vaghani](https://github.com/meetvaghani12)**

⭐ Star this repo if you found it useful!

</div>
