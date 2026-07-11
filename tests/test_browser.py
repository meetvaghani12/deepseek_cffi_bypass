import pytest
from src.browser.launcher import launch_browser
from src.browser.context import create_context

def test_launch_browser():
    browser = launch_browser(headless=True)
    assert browser is not None
    browser.close()

def test_create_context():
    browser = launch_browser(headless=True)
    context = create_context(browser)
    assert context is not None
    context.close()
    browser.close()