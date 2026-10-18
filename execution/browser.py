
"""Read-only webpage fetching and text extraction for PHANES."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from execution.tool_registry import ToolRegistry, ToolSafetyLevel


class BrowserToolError(Exception):
    """Raised when a browser operation is invalid or unsafe."""


class BrowserTools:
    """Fetch public webpages and extract readable text."""

    def __init__(
        self,
        timeout_seconds: int = 10,
        max_response_bytes: int = 1_000_000,
        max_text_chars: int = 20_000,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise ValueError("timeout_seconds must be an integer.")

        if not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30.")

        if isinstance(max_response_bytes, bool) or not isinstance(
            max_response_bytes, int
        ):
            raise ValueError("max_response_bytes must be an integer.")

        if not 1 <= max_response_bytes <= 5_000_000:
            raise ValueError("max_response_bytes must be between 1 and 5,000,000.")

        if isinstance(max_text_chars, bool) or not isinstance(max_text_chars, int):
            raise ValueError("max_text_chars must be an integer.")

        if not 1 <= max_text_chars <= 100_000:
            raise ValueError("max_text_chars must be between 1 and 100,000.")

        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_text_chars = max_text_chars

    @staticmethod
    def _validate_url(url: str) -> str:
        """Accept only public HTTP(S) URLs and reject local/private destinations."""
        if not isinstance(url, str) or not url.strip():
            raise BrowserToolError("URL must be a non-empty string.")

        parsed = urlparse(url.strip())

        if parsed.scheme.lower() not in {"http", "https"}:
            raise BrowserToolError("Only HTTP and HTTPS URLs are allowed.")

        if not parsed.hostname:
            raise BrowserToolError("URL must contain a hostname.")

        if parsed.username is not None or parsed.password is not None:
            raise BrowserToolError("URLs containing credentials are not allowed.")

        hostname = parsed.hostname.rstrip(".").lower()

        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise BrowserToolError("Localhost URLs are not allowed.")

        try:
            addresses = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except (OSError, ValueError) as exc:
            raise BrowserToolError("The hostname could not be resolved.") from exc

        if not addresses:
            raise BrowserToolError("The hostname did not resolve to an address.")

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
                    "URLs resolving to private or non-public addresses are not allowed."
                )

        return url.strip()

    def fetch_page(self, url: str) -> dict[str, object]:
        """Fetch a webpage and return its title and extracted text."""
        current_url = self._validate_url(url)

        # Redirects are handled manually so each destination can be validated.
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
                        if response.is_redirect or response.is_permanent_redirect:
                            location = response.headers.get("Location")

                            if not location:
                                raise BrowserToolError(
                                    "The server returned a redirect without a destination."
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

                        if not (
                            "text/html" in content_type
                            or "application/xhtml+xml" in content_type
                            or "text/plain" in content_type
                        ):
                            raise BrowserToolError(
                                "The URL did not return HTML or plain text."
                            )

                        body = bytearray()

                        for chunk in response.iter_content(chunk_size=8192):
                            if not chunk:
                                continue

                            remaining = self.max_response_bytes - len(body)

                            if len(chunk) > remaining:
                                body.extend(chunk[:remaining])
                                break

                            body.extend(chunk)

                            if len(body) >= self.max_response_bytes:
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

                            if soup.title and soup.title.string:
                                title = soup.title.string.strip()
                            else:
                                title = None

                            for element in soup(
                                ["script", "style", "noscript", "svg"]
                            ):
                                element.decompose()

                            text = soup.get_text(separator=" ", strip=True)

                        return {
                            "success": True,
                            "url": current_url,
                            "status_code": response.status_code,
                            "title": title,
                            "text": text[: self.max_text_chars],
                            "error": None,
                        }

                raise BrowserToolError("The webpage exceeded the redirect limit.")

        except requests.Timeout as exc:
            raise BrowserToolError("The webpage request timed out.") from exc
        except requests.RequestException as exc:
            raise BrowserToolError("The webpage could not be retrieved.") from exc


def register_browser_tools(registry: ToolRegistry) -> BrowserTools:
    """Register read-only browser tools."""
    if not isinstance(registry, ToolRegistry):
        raise TypeError("registry must be a ToolRegistry instance.")

    browser = BrowserTools()
    registry.register(
        "browser.fetch_page",
        browser.fetch_page,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    return browser
