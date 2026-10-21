import pytest
from unittest.mock import Mock

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


def test_registers_all_browser_tools():
    registry = ToolRegistry()
    register_browser_tools(registry)

    expected = {
        "browser.fetch_page",
        "browser.open_page",
        "browser.inspect_page",
        "browser.close_page",
        "browser.click",
        "browser.fill",
    }

    assert expected.issubset(set(registry.list_tools()))


def test_browser_interactions_require_approval():
    registry = ToolRegistry()
    register_browser_tools(registry)
    executor = Executor(registry)

    click_result = executor.execute(
        "browser.click",
        {"selector": "button[type=submit]"},
    )
    fill_result = executor.execute(
        "browser.fill",
        {"selector": "input[name=email]", "value": "test@example.com"},
    )

    assert click_result.success is False
    assert click_result.error_code == "APPROVAL_REQUIRED"
    assert fill_result.success is False
    assert fill_result.error_code == "APPROVAL_REQUIRED"


def test_browser_inspection_requires_an_open_page(browser):
    with pytest.raises(BrowserToolError):
        browser.inspect_page()


def test_browser_click_requires_an_open_page(browser):
    with pytest.raises(BrowserToolError):
        browser.click("button")


def test_browser_fill_requires_an_open_page(browser):
    with pytest.raises(BrowserToolError):
        browser.fill("input[name=email]", "test@example.com")


def test_browser_rejects_empty_selector(browser):
    with pytest.raises(BrowserToolError):
        browser.click(" ")


def test_browser_fill_rejects_non_string_value(browser):
    with pytest.raises(BrowserToolError):
        browser.fill("input[name=email]", 123)


def test_browser_fill_rejects_oversized_value(browser):
    with pytest.raises(BrowserToolError):
        browser.fill("input[name=email]", "x" * 10_001)


def test_open_page_success(monkeypatch, browser):
    page = Mock()
    page.goto.return_value = Mock(status=200)
    page.url = "https://example.com"
    page.title.return_value = "Example"

    monkeypatch.setattr(browser, "_ensure_page", lambda: page)
    monkeypatch.setattr(browser, "_validate_url", lambda url: url)

    result = browser.open_page("https://example.com")

    assert result["success"] is True
    assert result["url"] == "https://example.com"
    assert result["title"] == "Example"
    assert result["status_code"] == 200
    page.goto.assert_called_once()


def test_inspect_page_success(browser):
    page = Mock()
    page.is_closed.return_value = False
    page.url = "https://example.com"
    page.title.return_value = "Example"
    page.locator.return_value.inner_text.return_value = "Hello PHANES"
    browser._page = page

    result = browser.inspect_page()

    assert result["success"] is True
    assert result["title"] == "Example"
    assert result["text"] == "Hello PHANES"


def test_click_success(browser):
    page = Mock()
    page.is_closed.return_value = False
    page.url = "https://example.com"
    page.title.return_value = "Example"
    browser._page = page

    result = browser.click("button#continue")

    assert result["success"] is True
    page.locator.assert_called_with("button#continue")
    page.locator.return_value.first.click.assert_called_once()


def test_fill_success(browser):
    page = Mock()
    page.is_closed.return_value = False
    page.url = "https://example.com"
    page.title.return_value = "Example"
    browser._page = page

    result = browser.fill("input[name=email]", "test@example.com")

    assert result["success"] is True
    page.locator.assert_called_with("input[name=email]")
    page.locator.return_value.first.fill.assert_called_once_with(
        "test@example.com",
        timeout=browser.timeout_seconds * 1000,
    )


def test_close_page_success(browser):
    context = Mock()
    browser_instance = Mock()
    playwright = Mock()

    browser._context = context
    browser._browser = browser_instance
    browser._playwright = playwright
    browser._page = Mock()

    result = browser.close_page()

    assert result == {"success": True, "closed": True}
    context.close.assert_called_once()
    browser_instance.close.assert_called_once()
    playwright.stop.assert_called_once()
    assert browser._page is None


def test_cdp_guard_blocks_private_redirect_destination(browser):
    # Exercise the actual CDP request callback installed by _ensure_page().
    # The live redirect diagnostic separately verifies Chromium invokes it
    # when following the tested HTTP redirect.
    context = Mock()
    page = Mock()
    cdp_session = Mock()

    browser._playwright = Mock()
    browser._browser = Mock()
    browser._browser.is_connected.return_value = True
    browser._browser.new_context.return_value = context
    context.new_page.return_value = page
    context.new_cdp_session.return_value = cdp_session

    browser._ensure_page()

    event_name, guard_callback = cdp_session.on.call_args.args
    assert event_name == "Fetch.requestPaused"

    guard_callback(
        {
            "requestId": "request-123",
            "request": {"url": "http://127.0.0.1/"},
        }
    )

    cdp_session.send.assert_any_call(
        "Fetch.failRequest",
        {
            "requestId": "request-123",
            "errorReason": "BlockedByClient",
        },
    )
