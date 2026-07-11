from config.settings import settings

class Endpoints:
    # Placeholder endpoints – replace with your actual test server
    BASE_URL = settings.API_BASE_URL
    CHAT = settings.CHAT_ENDPOINT

    @classmethod
    def chat_completion(cls):
        return cls.CHAT