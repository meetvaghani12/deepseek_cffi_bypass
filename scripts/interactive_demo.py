#!/usr/bin/env python
import logging
from src.api.client import DeepSeekClient

logging.basicConfig(level=logging.INFO, format="%(name)s:%(levelname)s:%(message)s")

logger = logging.getLogger(__name__)


def main():
    print("Initializing browser session and logging in...")
    client = DeepSeekClient()

    print("\nInteractive demo — type 'exit' to quit.")
    print("(Use empty line + Enter to send multi-line input)\n")
    try:
        while True:
            msg = input("You: ").strip()
            if not msg:
                continue
            if msg.lower() == "exit":
                break
            try:
                resp = client.chat(msg)
                print(f"Bot: {resp.choices[0].message.content}\n")
            except Exception as e:
                logger.error("Chat error: %s", e)
                print(f"Error: {e}\n")
    finally:
        client.session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
