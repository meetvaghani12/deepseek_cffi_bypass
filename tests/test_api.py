import pytest
from src.api.client import DeepSeekClient

def test_client():
    client = DeepSeekClient()
    response = client.chat("Hello")
    assert response is not None
    assert hasattr(response, "choices")