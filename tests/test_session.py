import pytest
from src.network.session import PersistentSession

def test_session_creation():
    session = PersistentSession()
    assert session.tls_session is not None
    # Just check that we have a session object
    assert hasattr(session, "get")