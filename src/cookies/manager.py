import json
import os
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class CookieManager:
    def __init__(self, session_file: str = "data/session.json"):
        self.session_file = session_file
        self.session_data = {
    "cookies": {},
    "auth_token": None,
    "pow_response": None,
    "hif_leim": None,
    "chat_session_id": None,
    "parent_message_id": None,
    "expiry": None
}
        self.load()

    def load(self) -> Dict:
        """Load the full session data from disk."""
        try:
            with open(self.session_file, "r") as f:
                data = json.load(f)
                self.session_data = {
                    "cookies": data.get("cookies", {}),
                    "auth_token": data.get("auth_token"),
                    "pow_response": data.get("pow_response"),
                    "hif_leim": data.get("hif_leim"),
                    "chat_session_id": data.get("chat_session_id"),
                    "parent_message_id": data.get("parent_message_id"),
                    "expiry": data.get("expiry")
                }
                logger.info("Loaded session from %s", self.session_file)
        except FileNotFoundError:
            logger.info("No session file found; starting fresh.")
            self.session_data = {
                "cookies": {},
                "auth_token": None,
                "pow_response": None,
                "hif_leim": None,
                "chat_session_id": None,
                "parent_message_id": None,
                "expiry": None
            }
        return self.session_data

    def save(self, session_data: Dict):
        """Save full session data to disk."""
        # Ensure all keys exist
        self.session_data.update(session_data)
        # Set expiry to now + 30 minutes (or from token expiry if available)
        if not self.session_data.get("expiry"):
            self.session_data["expiry"] = (datetime.now() + timedelta(minutes=30)).isoformat()
        with open(self.session_file, "w") as f:
            json.dump(self.session_data, f, indent=2)
        logger.info("Session saved to %s", self.session_file)

    def is_expired(self) -> bool:
        """Check if the stored session is expired (or about to expire)."""
        expiry_str = self.session_data.get("expiry")
        if not expiry_str:
            return True
        expiry = datetime.fromisoformat(expiry_str)
        from config.settings import settings
        margin = settings.COOKIE_REFRESH_MARGIN
        return datetime.now() + timedelta(seconds=margin) >= expiry

    # Convenience properties
    @property
    def cookies(self) -> Dict:
        return self.session_data.get("cookies", {})

    @property
    def auth_token(self) -> Optional[str]:
        return self.session_data.get("auth_token")

    @property
    def pow_response(self) -> Optional[str]:
        return self.session_data.get("pow_response")

    @property
    def hif_leim(self) -> Optional[str]:
        return self.session_data.get("hif_leim")

    @property
    def chat_session_id(self) -> Optional[str]:
        return self.session_data.get("chat_session_id")

    @property
    def parent_message_id(self) -> Optional[str]:
        return self.session_data.get("parent_message_id")