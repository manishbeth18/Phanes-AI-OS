
import pytest

from shared.llm.provider_manager import (
    LLMProviderManager,
    LLMResponse,
    ProviderError,
)


class FakeProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_gemini_success_does_not_call_fallback():
    gemini = FakeProvider(
        result=LLMResponse("Gemini response", "gemini", "test-model")
    )
    deepseek = FakeProvider(
        result=LLMResponse("DeepSeek response", "openrouter", "test-model")
    )
    manager = LLMProviderManager(primary=gemini, fallback=deepseek)

    result = manager.generate("Summarize this project")

    assert result.text == "Gemini response"
    assert result.provider == "gemini"
    assert gemini.calls == 1
    assert deepseek.calls == 0


def test_gemini_quota_failure_uses_deepseek():
    gemini = FakeProvider(
        error=ProviderError(
            "Quota exhausted",
            retryable=True,
            quota_exhausted=True,
        )
    )
    deepseek = FakeProvider(
        result=LLMResponse("Fallback response", "openrouter", "test-model")
    )
    manager = LLMProviderManager(primary=gemini, fallback=deepseek)

    result = manager.generate("Summarize this project")

    assert result.text == "Fallback response"
    assert result.provider == "openrouter"
    assert gemini.calls == 1
    assert deepseek.calls == 1


def test_both_providers_fail_uses_local_fallback():
    gemini = FakeProvider(error=ProviderError("Gemini unavailable"))
    deepseek = FakeProvider(error=ProviderError("OpenRouter unavailable"))
    manager = LLMProviderManager(
        primary=gemini,
        fallback=deepseek,
        local_fallback=lambda prompt: "Local deterministic response",
    )

    result = manager.generate("Summarize this project")

    assert result.text == "Local deterministic response"
    assert result.provider == "local"
    assert gemini.calls == 1
    assert deepseek.calls == 1


def test_all_providers_fail_without_local_fallback_raises():
    manager = LLMProviderManager(
        primary=FakeProvider(error=ProviderError("Gemini unavailable")),
        fallback=FakeProvider(error=ProviderError("OpenRouter unavailable")),
    )

    with pytest.raises(ProviderError, match="All LLM providers failed"):
        manager.generate("Summarize this project")


def test_missing_provider_can_use_other_provider():
    deepseek = FakeProvider(
        result=LLMResponse("Fallback response", "openrouter", "test-model")
    )
    manager = LLMProviderManager(primary=None, fallback=deepseek)

    # The manager normally constructs missing providers from environment
    # variables. Patch the factories to avoid real API configuration.
    manager.primary = None
    result = manager.generate("Summarize this project")

    assert result.text == "Fallback response"
    assert result.provider == "openrouter"
    assert deepseek.calls == 1
