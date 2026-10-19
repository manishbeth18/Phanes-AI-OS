
"""Safe webpage fetching and browser automation for PHANES."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from execution.tool_registry import ToolRegistry, ToolSafetyLevel


class BrowserToolError(Exception):
    """Raised when a browser operation is invalid or unsafe."""


class BrowserTools:
    """Fetch public webpages and operate a persistent Chromium page."""

    def __init__(
        self,
        timeout_seconds: int = 10,
        max_response_bytes: int = 1_000_000,
        max_text_chars: int = 20_000,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, int
        ):
            raise ValueError("timeout_seconds must be an integer.")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30.")

        if isinstance(max_response_bytes, bool) or not isinstance(
            max_response_bytes, int
        ):
            raise ValueError("max_response_bytes must be an integer.")
        if not 1 <= max_response_bytes <= 5_000_000:
            raise ValueError(
                "max_response_bytes must be between 1 and 5,000,000."
            )

        if isinstance(max_text_chars, bool) or not isinstance(
            max_text_chars, int
        ):
            raise ValueError("max_text_chars must be an integer.")
        if not 1 <= max_text_chars <= 100_000:
            raise ValueError(
                "max_text_chars must be between 1 and 100,000."
            )

        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_text_chars = max_text_chars

        # Created lazily so ordinary fetch_page calls don't need Chromium.
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp_session = None

    @staticmethod
    def _validate_url(url: str) -> str:
        """Accept public HTTP(S) URLs; reject local/private destinations."""
        if not isinstance(url, str) or not url.strip():
            raise BrowserToolError("URL must be a non-empty string.")

        url = url.strip()
        parsed = urlparse(url)

        if parsed.scheme.lower() not in {"http", "https"}:
            raise BrowserToolError("Only HTTP and HTTPS URLs are allowed.")

        if not parsed.hostname:
            raise BrowserToolError("URL must contain a hostname.")

        if parsed.username is not None or parsed.password is not None:
            raise BrowserToolError(
                "URLs containing credentials are not allowed."
            )

        hostname = parsed.hostname.rstrip(".").lower()

        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise BrowserToolError("Localhost URLs are not allowed.")

        try:
            addresses = socket.getaddrinfo(
                hostname,
                parsed.port
                or (443 if parsed.scheme.lower() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except (OSError, ValueError) as exc:
            raise BrowserToolError(
                "The hostname could not be resolved."
            ) from exc

        if not addresses:
            raise BrowserToolError(
                "The hostname did not resolve to an address."
            )

        for address in addresses:
            ip_text = address[4][0].split("%", 1)[0]

            try:
                ip = ipaddress.ip_address(ip_text)
            except ValueError as exc:
                raise BrowserToolError(
                    "The hostname resolved to an invalid address."
                ) from exc

            if not ip.is_global:
                raise BrowserToolError(
                    "URLs resolving to private or non-public "
                    "addresses are not allowed."
                )

        return url

    def fetch_page(self, url: str) -> dict[str, object]:
        """Fetch a public webpage and return its title and readable text."""
        current_url = self._validate_url(url)
        headers = {
            "User-Agent": "PHANES-ReadOnlyBot/0.1",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
        }

        try:
            with requests.Session() as session:
                session.trust_env = False

                for _ in range(6):
                    current_url = self._validate_url(current_url)

                    with session.get(
                        current_url,
                        headers=headers,
                        timeout=self.timeout_seconds,
                        allow_redirects=False,
                        stream=True,
                    ) as response:
                        if (
                            response.is_redirect
                            or response.is_permanent_redirect
                        ):
                            location = response.headers.get("Location")
                            if not location:
                                raise BrowserToolError(
                                    "Redirect has no destination."
                                )
                            current_url = urljoin(current_url, location)
                            continue

                        if response.status_code >= 400:
                            return {
                                "success": False,
                                "url": current_url,
                                "status_code": response.status_code,
                                "title": None,
                                "text": "",
                                "error": "The webpage returned an HTTP error.",
                            }

                        content_type = response.headers.get(
                            "Content-Type", ""
                        ).lower()

                        if not any(
                            kind in content_type
                            for kind in (
                                "text/html",
                                "application/xhtml+xml",
                                "text/plain",
                            )
                        ):
                            raise BrowserToolError(
                                "The URL did not return HTML or plain text."
                            )

                        body = bytearray()

                        for chunk in response.iter_content(
                            chunk_size=8192
                        ):
                            if not chunk:
                                continue

                            remaining = (
                                self.max_response_bytes - len(body)
                            )
                            if remaining <= 0:
                                break

                            body.extend(chunk[:remaining])

                            if len(chunk) > remaining:
                                break

                        content = bytes(body).decode(
                            response.encoding or "utf-8",
                            errors="replace",
                        )

                        if "text/plain" in content_type:
                            title = None
                            text = content
                        else:
                            soup = BeautifulSoup(content, "html.parser")
                            title = (
                                soup.title.get_text(strip=True)
                                if soup.title
                                else None
                            )

                            for element in soup(
                                ["script", "style", "noscript", "svg"]
                            ):
                                element.decompose()

                            text = soup.get_text(
                                separator=" ", strip=True
                            )

                        return {
                            "success": True,
                            "url": current_url,
                            "status_code": response.status_code,
                            "title": title,
                            "text": text[: self.max_text_chars],
                            "error": None,
                        }

                raise BrowserToolError(
                    "The webpage exceeded the redirect limit."
                )

        except requests.Timeout as exc:
            raise BrowserToolError(
                "The webpage request timed out."
            ) from exc
        except requests.RequestException as exc:
            raise BrowserToolError(
                "The webpage could not be retrieved."
            ) from exc

    def _ensure_page(self):
        if self._page is not None and not self._page.is_closed():
            return

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        if self._browser is None or not self._browser.is_connected():
            self._browser = self._playwright.chromium.launch(
                headless=True,
            )

        self._context = self._browser.new_context(
            accept_downloads=False,
            service_workers="block",
        )
        self._context.set_default_timeout(self.timeout_seconds * 1000)

        self._page = self._context.new_page()

        # Block unsafe request destinations at Chromium's network layer.
        self._cdp_session = self._context.new_cdp_session(self._page)

        def guard_cdp_request(params):
            request_id = params["requestId"]
            request_url = params["request"]["url"]

            try:
                self._validate_url(request_url)
            except Exception as exc:
                print(
                    "CDP GUARD REJECTED:",
                    request_url,
                    "|",
                    str(exc),
                )
                self._cdp_session.send(
                    "Fetch.failRequest",
                    {
                        "requestId": request_id,
                        "errorReason": "BlockedByClient",
                    },
                )
            else:
                print("CDP GUARD ALLOWED:", request_url)
                self._cdp_session.send(
                    "Fetch.continueRequest",
                    {"requestId": request_id},
                )

        self._cdp_session.on(
            "Fetch.requestPaused",
            guard_cdp_request,
        )

        self._cdp_session.send(
            "Fetch.enable",
            {
                "patterns": [
                    {
                        "urlPattern": "*",
                        "requestStage": "Request",
                    }
                ]
            },
        )

        # Popups are not supported by this tool.
        self._page.on("popup", lambda popup: popup.close())

    def _guard_request(self, route) -> None:
        """Block requests whose destinations fail URL safety validation."""
        try:
            self._validate_url(route.request.url)
            route.continue_()
        except Exception:
            route.abort()

    def open_page(self, url: str) -> dict[str, object]:
        """Navigate Chromium to a validated public HTTP(S) URL."""
        url = self._validate_url(url)
        page = self._ensure_page()
        if page is None:
            page = self._require_page()

        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.timeout_seconds * 1000,
            )
            return {
                "success": True,
                "url": page.url,
                "title": page.title(),
                "status_code": response.status if response else None,
            }
        except Exception as exc:
            raise BrowserToolError(
                "Browser navigation failed or timed out."
            ) from exc

    def inspect_page(self) -> dict[str, object]:
        """Return the current page URL, title, and visible text."""
        page = self._require_page()

        try:
            title = page.title()
            url = page.url
            text = page.locator("body").inner_text(
                timeout=self.timeout_seconds * 1000
            )
            return {
                "success": True,
                "url": url,
                "title": title,
                "text": text[: self.max_text_chars],
            }
        except Exception as exc:
            raise BrowserToolError(
                "Could not inspect the current webpage."
            ) from exc

    def click(self, selector: str) -> dict[str, object]:
        """Click the first matching element. Register as approval-required."""
        selector = self._validate_selector(selector)
        page = self._require_page()

        try:
            page.locator(selector).first.click(
                timeout=self.timeout_seconds * 1000
            )
            return {
                "success": True,
                "url": page.url,
                "title": page.title(),
            }
        except Exception as exc:
            raise BrowserToolError(
                "Could not click the requested element."
            ) from exc

    def fill(self, selector: str, value: str) -> dict[str, object]:
        """Fill a field. Register as approval-required."""
        selector = self._validate_selector(selector)

        if not isinstance(value, str):
            raise BrowserToolError("Field value must be a string.")
        if len(value) > 10_000:
            raise BrowserToolError("Field value is too long.")

        page = self._require_page()

        try:
            page.locator(selector).first.fill(
                value,
                timeout=self.timeout_seconds * 1000,
            )
            return {
                "success": True,
                "url": page.url,
                "title": page.title(),
            }
        except Exception as exc:
            raise BrowserToolError(
                "Could not fill the requested field."
            ) from exc

    @staticmethod
    def _validate_selector(selector: str) -> str:
        if not isinstance(selector, str) or not selector.strip():
            raise BrowserToolError(
                "Selector must be a non-empty string."
            )
        if len(selector) > 2_000:
            raise BrowserToolError("Selector is too long.")
        return selector.strip()

    def _require_page(self):
        if self._page is None or self._page.is_closed():
            raise BrowserToolError(
                "No browser page is open. Call browser.open_page first."
            )
        return self._page

    def close_page(self) -> dict[str, object]:
        """Close the browser session and release its resources."""
        errors = []

        # Detach the network interception session before closing its page/context.
        if self._cdp_session is not None:
            try:
                self._cdp_session.detach()
            except Exception as exc:
                errors.append(exc)
            self._cdp_session = None

        for resource in (
            self._context,
            self._browser,
            self._playwright,
        ):
            if resource is None:
                continue
            try:
                if resource is self._playwright:
                    resource.stop()
                else:
                    resource.close()
            except Exception as exc:
                errors.append(exc)

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._cdp_session = None

        if errors:
            raise BrowserToolError(
                "The browser session could not be fully closed."
            ) from errors[0]

        return {"success": True, "closed": True}


def register_browser_tools(registry: ToolRegistry) -> BrowserTools:
    """Register browser fetching, navigation, inspection, and interactions."""
    if not isinstance(registry, ToolRegistry):
        raise TypeError("registry must be a ToolRegistry instance.")

    browser = BrowserTools()

    registry.register(
        "browser.fetch_page",
        browser.fetch_page,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "browser.open_page",
        browser.open_page,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "browser.inspect_page",
        browser.inspect_page,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "browser.close_page",
        browser.close_page,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "browser.click",
        browser.click,
        safety_level=ToolSafetyLevel.APPROVAL_REQUIRED,
    )
    registry.register(
        "browser.fill",
        browser.fill,
        safety_level=ToolSafetyLevel.APPROVAL_REQUIRED,
    )

    return browser
