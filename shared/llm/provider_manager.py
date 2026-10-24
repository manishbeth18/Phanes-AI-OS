
"""LLM provider management for PHANES.

Primary: Google Gemini
Fallback: DeepSeek through OpenRouter
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from google import genai
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


# Load credentials from the PHANES project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """A provider failed to generate a response."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        quota_exhausted: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.quota_exhausted = quota_exhausted


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str


class GeminiProvider:
    """Google Gemini primary provider."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
    ) -> None:
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ProviderError("GEMINI_API_KEY is not configured.")

        self.client = genai.Client(api_key=key)
        self.model = model

    def generate(self, prompt: str) -> LLMResponse:
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = (response.text or "").strip()
            if not text:
                raise ProviderError("Gemini returned an empty response.")
            return LLMResponse(text, "gemini", self.model)
        except ProviderError:
            raise
        except Exception as exc:
            # Gemini SDK exceptions vary by version. Inspect the HTTP status
            # and structured error message without exposing credentials.
            status = getattr(exc, "code", None)
            status = status or getattr(exc, "status_code", None)
            message = str(exc).lower()
            quota = (
                status == 429
                and any(word in message for word in (
                    "quota", "daily", "resource_exhausted",
                    "limit: 0", "free_tier"
                ))
            )
            retryable = status in (408, 429, 500, 502, 503, 504)
            raise ProviderError(
                f"Gemini request failed ({status or type(exc).__name__}).",
                retryable=retryable,
                quota_exhausted=quota,
            ) from exc


class OpenRouterProvider:
    """DeepSeek fallback through OpenRouter's OpenAI-compatible API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek/deepseek-chat",
    ) -> None:
        key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise ProviderError("OPENROUTER_API_KEY is not configured.")

        self.client = OpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            timeout=30.0,
            max_retries=0,
        )
        self.model = model

    def generate(self, prompt: str) -> LLMResponse:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise ProviderError("OpenRouter returned an empty response.")
            return LLMResponse(text, "openrouter", self.model)
        except ProviderError:
            raise
        except (APITimeoutError, APIConnectionError) as exc:
            raise ProviderError(
                "OpenRouter connection or timeout failure.",
                retryable=True,
            ) from exc
        except APIStatusError as exc:
            status = exc.status_code
            raise ProviderError(
                f"OpenRouter request failed (HTTP {status}).",
                retryable=status in (408, 429, 500, 502, 503, 504),
            ) from exc


class LLMProviderManager:
    """Use Gemini first, then OpenRouter, then an optional local fallback."""

    def __init__(
        self,
        primary: object | None = None,
        fallback: object | None = None,
        local_fallback: Callable[[str], str] | None = None,
    ) -> None:
        self.primary = primary or self._make_gemini()
        self.fallback = fallback or self._make_openrouter()
        self.local_fallback = local_fallback

    @staticmethod
    def _make_gemini() -> GeminiProvider | None:
        try:
            return GeminiProvider()
        except ProviderError as exc:
            logger.warning("Gemini unavailable: %s", exc)
            return None

    @staticmethod
    def _make_openrouter() -> OpenRouterProvider | None:
        try:
            return OpenRouterProvider()
        except ProviderError as exc:
            logger.warning("OpenRouter unavailable: %s", exc)
            return None

    def generate(self, prompt: str) -> LLMResponse:
        failures: list[str] = []

        for name, provider in (
            ("Gemini", self.primary),
            ("OpenRouter", self.fallback),
        ):
            if provider is None:
                failures.append(f"{name} is not configured.")
                continue

            try:
                result = provider.generate(prompt)
                logger.info("LLM response generated by %s", result.provider)
                return result
            except ProviderError as exc:
                failures.append(f"{name}: {exc}")
                logger.warning("%s failed: %s", name, exc)

                # Quota exhaustion should switch immediately. Other
                # transient failures get one bounded retry in a later patch.
                continue
            except Exception:
                failures.append(f"{name}: unexpected provider failure.")
                logger.exception("%s failed unexpectedly", name)

        if self.local_fallback is not None:
            text = self.local_fallback(prompt).strip()
            if text:
                return LLMResponse(text, "local", "deterministic")

        raise ProviderError(
            "All LLM providers failed. " + " ".join(failures)
        )
