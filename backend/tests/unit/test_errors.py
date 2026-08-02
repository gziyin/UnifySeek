from __future__ import annotations

from ai_dev_researcher.core.errors import AppError, SearchProviderError


def test_search_provider_error_is_app_error():
    err = SearchProviderError("tavily search failed")
    assert isinstance(err, AppError)
    assert err.code == "SEARCH_PROVIDER_ERROR"
    assert err.status_code == 502
    assert err.retryable is True


def test_search_provider_error_carries_provider_field():
    err = SearchProviderError("timeout", provider="tavily")
    assert err.provider == "tavily"


def test_search_provider_error_overrides_code_and_retryable():
    err = SearchProviderError(
        "client unavailable",
        provider="tavily",
        code="TAVILY_UNAVAILABLE",
        retryable=False,
    )
    assert err.code == "TAVILY_UNAVAILABLE"
    assert err.retryable is False
    assert err.provider == "tavily"
