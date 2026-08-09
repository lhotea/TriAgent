"""Tests for triagent.summarizer — DailyBrief model and fallback logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import pytest

from triagent.summarizer import FALLBACK_MODEL, DailyBrief, Headline, Summarizer


def test_headline_creation():
    """Headline can be instantiated with required fields."""
    h = Headline(
        title="Test headline here",
        one_liner="This is why it matters.",
        source="TestSource",
    )
    assert h.title == "Test headline here"
    assert h.one_liner == "This is why it matters."
    assert h.source == "TestSource"


def test_daily_brief_model(sample_brief):
    """DailyBrief validates all required fields."""
    assert sample_brief.hook == "Kemenz takes Kona in a photo finish"
    assert len(sample_brief.headlines) == 3
    assert len(sample_brief.hashtags) == 6
    assert "triathlon" in sample_brief.hashtags


def test_daily_brief_min_max_headlines():
    """DailyBrief enforces min_length=3 and max_length=5 on headlines."""
    with pytest.raises(Exception):  # Pydantic validation error
        DailyBrief(
            hook="Short hook",
            headlines=[
                Headline(title="One", one_liner="Why", source="S"),
                Headline(title="Two", one_liner="Why", source="S"),
            ],
            caption_body="Body",
            engagement_prompt="Question?",
            hashtags=["t1", "t2", "t3", "t4", "t5", "t6"],
        )

    with pytest.raises(Exception):  # Pydantic validation error
        DailyBrief(
            hook="Short hook",
            headlines=[
                Headline(title="One", one_liner="Why", source="S"),
                Headline(title="Two", one_liner="Why", source="S"),
                Headline(title="Three", one_liner="Why", source="S"),
                Headline(title="Four", one_liner="Why", source="S"),
                Headline(title="Five", one_liner="Why", source="S"),
                Headline(title="Six", one_liner="Why", source="S"),
            ],
            caption_body="Body",
            engagement_prompt="Question?",
            hashtags=["t1", "t2", "t3", "t4", "t5", "t6"],
        )


def test_daily_brief_min_max_hashtags():
    """DailyBrief enforces min_length=15 and max_length=25 on hashtags."""
    with pytest.raises(Exception):  # Pydantic validation error
        DailyBrief(
            hook="Short hook",
            headlines=[
                Headline(title="One", one_liner="Why", source="S"),
                Headline(title="Two", one_liner="Why", source="S"),
                Headline(title="Three", one_liner="Why", source="S"),
            ],
            caption_body="Body",
            engagement_prompt="Question?",
            hashtags=["tag1", "tag2"],  # Too few (min 6)
        )

    with pytest.raises(Exception):  # Pydantic validation error
        DailyBrief(
            hook="Short hook",
            headlines=[
                Headline(title="One", one_liner="Why", source="S"),
                Headline(title="Two", one_liner="Why", source="S"),
                Headline(title="Three", one_liner="Why", source="S"),
            ],
            caption_body="Body",
            engagement_prompt="Question?",
            hashtags=[f"tag{i}" for i in range(30)],  # Too many (max 10)
        )


def test_summarizer_init():
    """Summarizer stores model and fallback model."""
    with patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")
        assert summ.model == "claude-opus-4-7"
        assert summ.fallback_model == FALLBACK_MODEL

        mock_client_cls.assert_called_once_with(api_key="test-key")


def test_summarizer_custom_models():
    """Summarizer accepts custom model names."""
    with patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        summ = Summarizer(
            api_key="test-key",
            model="custom-model",
            fallback_model="custom-fallback",
        )
        assert summ.model == "custom-model"
        assert summ.fallback_model == "custom-fallback"


def test_build_brief_success(sample_news_items):
    """build_brief calls the API and returns a DailyBrief on success."""
    mock_response = MagicMock()
    mock_response.parsed_output = MagicMock(spec=DailyBrief)
    mock_response.parsed_output.headlines = [MagicMock(spec=Headline)]
    mock_response.parsed_output.hashtags = ["tag1", "tag2", "tag3", "tag4", "tag5"]
    mock_response.stop_reason = "stop_sequence"

    with (
        patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls,
        patch("triagent.summarizer.log") as mock_log,
    ):
        mock_client = MagicMock()
        mock_client.messages.parse.return_value = mock_response
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")
        result = summ.build_brief(sample_news_items, brand_name="TestBrand")

        assert result is not None
        mock_client.messages.parse.assert_called_once()


def test_build_brief_fallback_on_connection_error(sample_news_items):
    """build_brief retries with fallback model on APIConnectionError."""
    error = anthropic.APIConnectionError(request=MagicMock(), message="Connection failed")

    mock_response = MagicMock()
    mock_response.parsed_output = MagicMock(spec=DailyBrief)
    mock_response.parsed_output.headlines = [MagicMock(spec=Headline)]
    mock_response.parsed_output.hashtags = ["tag1", "tag2", "tag3", "tag4", "tag5"]
    mock_response.stop_reason = "stop_sequence"

    with (
        patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls,
        patch("triagent.summarizer.log") as mock_log,
    ):
        mock_client = MagicMock()
        mock_client.messages.parse.side_effect = [error, mock_response]
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")
        result = summ.build_brief(sample_news_items, brand_name="TestBrand")

        assert result is not None
        # Should have been called twice: primary + fallback
        assert mock_client.messages.parse.call_count == 2
        # Second call should use fallback model
        calls = mock_client.messages.parse.call_args_list
        assert calls[1].kwargs["model"] == FALLBACK_MODEL


def test_build_brief_fallback_on_timeout(sample_news_items):
    """build_brief retries with fallback model on APITimeoutError."""
    error = anthropic.APITimeoutError(request=MagicMock())

    mock_response = MagicMock()
    mock_response.parsed_output = MagicMock(spec=DailyBrief)
    mock_response.parsed_output.headlines = [MagicMock(spec=Headline)]
    mock_response.parsed_output.hashtags = ["tag1", "tag2", "tag3", "tag4", "tag5"]
    mock_response.stop_reason = "stop_sequence"

    with (
        patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls,
        patch("triagent.summarizer.log") as mock_log,
    ):
        mock_client = MagicMock()
        mock_client.messages.parse.side_effect = [error, mock_response]
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")
        result = summ.build_brief(sample_news_items, brand_name="TestBrand")

        assert result is not None
        assert mock_client.messages.parse.call_count == 2


def test_build_brief_fallback_on_rate_limit(sample_news_items):
    """build_brief retries with fallback model on RateLimitError."""
    error = anthropic.RateLimitError(
        body="Rate limit exceeded", response=MagicMock(), message="Rate limited"
    )

    mock_response = MagicMock()
    mock_response.parsed_output = MagicMock(spec=DailyBrief)
    mock_response.parsed_output.headlines = [MagicMock(spec=Headline)]
    mock_response.parsed_output.hashtags = ["tag1", "tag2", "tag3", "tag4", "tag5"]
    mock_response.stop_reason = "stop_sequence"

    with (
        patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls,
        patch("triagent.summarizer.log") as mock_log,
    ):
        mock_client = MagicMock()
        mock_client.messages.parse.side_effect = [error, mock_response]
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")
        result = summ.build_brief(sample_news_items, brand_name="TestBrand")

        assert result is not None
        assert mock_client.messages.parse.call_count == 2


def test_build_brief_fallback_on_status_error(sample_news_items):
    """build_brief retries with fallback model on APIStatusError (5xx)."""
    error = anthropic.APIStatusError(
        message="Server error", response=MagicMock(), body="500 Internal Server Error"
    )

    mock_response = MagicMock()
    mock_response.parsed_output = MagicMock(spec=DailyBrief)
    mock_response.parsed_output.headlines = [MagicMock(spec=Headline)]
    mock_response.parsed_output.hashtags = ["tag1", "tag2", "tag3", "tag4", "tag5"]
    mock_response.stop_reason = "stop_sequence"

    with (
        patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls,
        patch("triagent.summarizer.log") as mock_log,
    ):
        mock_client = MagicMock()
        mock_client.messages.parse.side_effect = [error, mock_response]
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")
        result = summ.build_brief(sample_news_items, brand_name="TestBrand")

        assert result is not None
        assert mock_client.messages.parse.call_count == 2


def test_build_brief_raises_on_no_items():
    """build_brief raises ValueError when given no items."""
    with patch("triagent.summarizer.anthropic.Anthropic"):
        summ = Summarizer(api_key="test-key")

    with pytest.raises(ValueError, match="no news items"):
        summ.build_brief([], brand_name="TestBrand")


def test_build_brief_stores_parsed_output(sample_news_items):
    """build_brief returns the parsed output from the API response."""
    expected_brief = MagicMock(spec=DailyBrief)
    expected_brief.headlines = [MagicMock(spec=Headline)]
    expected_brief.hashtags = ["tag1", "tag2", "tag3", "tag4", "tag5"]

    mock_response = MagicMock()
    mock_response.parsed_output = expected_brief
    mock_response.stop_reason = "stop_sequence"

    with patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.messages.parse.return_value = mock_response
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")
        result = summ.build_brief(sample_news_items, brand_name="TestBrand")

        assert result is expected_brief


def test_build_brief_raises_on_none_parsed_output(sample_news_items):
    """build_brief raises RuntimeError when parsed_output is None."""
    mock_response = MagicMock()
    mock_response.parsed_output = None
    mock_response.stop_reason = "length"

    with patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.messages.parse.return_value = mock_response
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")

        with pytest.raises(RuntimeError, match="structured parse failed"):
            summ.build_brief(sample_news_items, brand_name="TestBrand")


def test_build_brief_raises_when_both_models_fail(sample_news_items):
    """build_brief raises RuntimeError when both primary and fallback fail."""
    error1 = anthropic.APIConnectionError(request=MagicMock(), message="Primary failed")
    error2 = anthropic.RateLimitError(
        body="Rate limited", response=MagicMock(), message="Fallback also rate limited"
    )

    with (
        patch("triagent.summarizer.anthropic.Anthropic") as mock_client_cls,
        patch("triagent.summarizer.log") as mock_log,
    ):
        mock_client = MagicMock()
        mock_client.messages.parse.side_effect = [error1, error2]
        mock_client_cls.return_value = mock_client

        summ = Summarizer(api_key="test-key")

        with pytest.raises(RuntimeError, match="both primary.*and fallback.*failed"):
            summ.build_brief(sample_news_items, brand_name="TestBrand")

        # Should have been called twice: primary + fallback
        assert mock_client.messages.parse.call_count == 2