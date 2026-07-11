#!/usr/bin/env python3
"""Launch the DeepSeek Chat web UI."""
import os, sys, logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(name)s:%(levelname)s:%(message)s")

from src.web.server import app
from src.web.server import get_client

print("\n  DeepSeek Chat — http://localhost:5050\n")
print("  Initializing browser session (one-time login)...")
get_client()
print("  Ready!\n")

app.run(host="127.0.0.1", port=5050, debug=False)
