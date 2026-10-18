
import pytest

from execution.browser import BrowserToolError, BrowserTools, register_browser_tools
from execution.executor import Executor
from execution.tool_registry import ToolRegistry, ToolSafetyLevel


@pytest.fixture
def browser():
    return BrowserTools()


def test_rejects_empty_url(browser):
    with pytest.raises(BrowserToolError):
        browser.fetch_page("")


def test_rejects_non_http_scheme(browser):
    with pytest.raises(BrowserToolError):
        browser.fetch_page("file:///C:/Windows/win.ini")


def test_rejects_localhost(browser):
    with pytest.raises(BrowserToolError):
        browser.fetch_page("http://localhost/")


def test_rejects_private_ip(browser):
    with pytest.raises(BrowserToolError):
        browser.fetch_page("http://127.0.0.1/")


def test_rejects_credentials_in_url(browser):
    with pytest.raises(BrowserToolError):
        browser.fetch_page("https://user:password@example.com/")


def test_rejects_invalid_timeout():
    with pytest.raises(ValueError):
        BrowserTools(timeout_seconds=0)


def test_registers_browser_tool():
    registry = ToolRegistry()
    register_browser_tools(registry)

    assert "browser.fetch_page" in registry


def test_browser_tool_can_be_called_through_executor(monkeypatch):
    registry = ToolRegistry()
    browser_tools = register_browser_tools(registry)

    def fake_fetch(url):
        return {
            "success": True,
            "url": url,
            "status_code": 200,
            "title": "Example",
            "text": "Hello from PHANES",
            "error": None,
        }

    monkeypatch.setattr(browser_tools, "fetch_page", fake_fetch)

    # Register the patched function in a fresh registry.
    registry = ToolRegistry()
    registry.register(
    "browser.fetch_page",
    browser_tools.fetch_page,
    safety_level=ToolSafetyLevel.READ_ONLY,
    )   

    result = Executor(registry).execute(
        "browser.fetch_page",
        {"url": "https://example.com"},
    )

    assert result.success is True
    assert result.output["title"] == "Example"
    assert "Hello from PHANES" in result.output["text"]
