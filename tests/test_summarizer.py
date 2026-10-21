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
        source_url="https://example.com/t",
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
                Headline(title="One", one_liner="Why", source="S", source_url="https://example.com/One"),
                Headline(title="Two", one_liner="Why", source="S", source_url="https://example.com/Two"),
            ],
            caption_body="Body",
            engagement_prompt="Question?",
            hashtags=["t1", "t2", "t3", "t4", "t5", "t6"],
        )

    with pytest.raises(Exception):  # Pydantic validation error
        DailyBrief(
            hook="Short hook",
            headlines=[
                Headline(title="One", one_liner="Why", source="S", source_url="https://example.com/One"),
                Headline(title="Two", one_liner="Why", source="S", source_url="https://example.com/Two"),
                Headline(title="Three", one_liner="Why", source="S", source_url="https://example.com/Three"),
                Headline(title="Four", one_liner="Why", source="S", source_url="https://example.com/Four"),
                Headline(title="Five", one_liner="Why", source="S", source_url="https://example.com/Five"),
                Headline(title="Six", one_liner="Why", source="S", source_url="https://example.com/Six"),
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
                Headline(title="One", one_liner="Why", source="S", source_url="https://example.com/One"),
                Headline(title="Two", one_liner="Why", source="S", source_url="https://example.com/Two"),
                Headline(title="Three", one_liner="Why", source="S", source_url="https://example.com/Three"),
            ],
            caption_body="Body",
            engagement_prompt="Question?",
            hashtags=["tag1", "tag2"],  # Too few (min 6)
        )

    with pytest.raises(Exception):  # Pydantic validation error
        DailyBrief(
            hook="Short hook",
            headlines=[
                Headline(title="One", one_liner="Why", source="S", source_url="https://example.com/One"),
                Headline(title="Two", one_liner="Why", source="S", source_url="https://example.com/Two"),
                Headline(title="Three", one_liner="Why", source="S", source_url="https://example.com/Three"),
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

class TestLeadStoryFields:
    """Slides 2 and 3 are the lead story's facts and meaning, so the model must supply them."""

    def _kwargs(self, **over):
        base = dict(
            hook="Short hook", hook_emphasis="Short", image_query="runner at dawn",
            headlines=[
                Headline(title="One", one_liner="Why", source="S", source_url="https://e/1"),
                Headline(title="Two", one_liner="Why", source="S", source_url="https://e/2"),
                Headline(title="Three", one_liner="Why", source="S", source_url="https://e/3"),
            ],
            takeaways=["A fact."], implication="Check your plan.",
            caption_body="Body", engagement_prompt="Q?", hashtags=[f"t{i}" for i in range(6)],
        )
        base.update(over)
        return base

    def test_valid_brief(self):
        brief = DailyBrief(**self._kwargs(takeaways=["One.", "Two.", "Three."]))
        assert len(brief.takeaways) == 3

    def test_takeaways_are_required_and_bounded(self):
        with pytest.raises(Exception):
            DailyBrief(**self._kwargs(takeaways=[]))  # slide 2 would be empty
        with pytest.raises(Exception):
            DailyBrief(**self._kwargs(takeaways=["a", "b", "c", "d"]))  # more than fits
        kwargs = self._kwargs()
        del kwargs["takeaways"]
        with pytest.raises(Exception):
            DailyBrief(**kwargs)

    def test_implication_is_required(self):
        kwargs = self._kwargs()
        del kwargs["implication"]
        with pytest.raises(Exception):
            DailyBrief(**kwargs)

    def test_both_are_in_the_schema_the_model_is_given(self):
        schema = DailyBrief.model_json_schema()
        assert {"takeaways", "implication"} <= set(schema["required"])

    def test_the_new_fields_follow_headlines_so_the_lead_is_chosen_first(self):
        order = list(DailyBrief.model_json_schema()["properties"])
        assert order.index("headlines") < order.index("takeaways") < order.index("implication")


class TestPromptRules:
    """The prompt cannot be unit-tested for quality, but its guard rails can be pinned."""

    def test_forbids_first_person(self):
        """The 23 Aug post turned a 220 Triathlon writer's 'I signed up for an AI coach'
        into 'I hired an AI coach for 8 weeks' under the account owner's name."""
        from triagent.summarizer import SYSTEM_PROMPT

        text = SYSTEM_PROMPT.lower()
        assert "never write in the first person" in text
        assert "report it as that writer's experience" in text

    def test_forbids_invented_facts(self):
        from triagent.summarizer import SYSTEM_PROMPT

        text = SYSTEM_PROMPT.lower()
        assert "must come from the summaries you were given" in text
        assert "fewer takeaways rather than padding" in text

    def test_describes_the_three_slide_structure(self):
        from triagent.summarizer import SYSTEM_PROMPT

        assert "three slides" in SYSTEM_PROMPT
        assert "`takeaways`" in SYSTEM_PROMPT and "`implication`" in SYSTEM_PROMPT

    def test_allows_addressing_the_reader_only_where_true(self):
        from triagent.summarizer import SYSTEM_PROMPT

        assert "never force it" in SYSTEM_PROMPT.lower()

    def test_the_model_sees_enough_of_each_summary_to_state_real_facts(self):
        """The takeaways may only use what is shown, so a thin excerpt invites padding."""
        import datetime as dt

        from triagent.news import NewsItem
        from triagent.summarizer import SUMMARY_CHARS

        assert SUMMARY_CHARS >= 600
        item = NewsItem(
            title="T", summary="a" * 300 + "b" * (SUMMARY_CHARS - 300) + "c" * 200,
            url="https://e/x", source="S", published=dt.datetime.now(dt.timezone.utc),
        )
        client = MagicMock()
        client.messages.parse.return_value = MagicMock(
            parsed_output=MagicMock(headlines=[], hashtags=[]), stop_reason="end_turn"
        )
        with patch("triagent.summarizer.anthropic.Anthropic", return_value=client):
            Summarizer(api_key="k").build_brief([item], brand_name="B")
        prompt = client.messages.parse.call_args.kwargs["messages"][0]["content"]
        # The excerpt is the line just above the url: exactly SUMMARY_CHARS long.
        excerpt = prompt.split("https://e/x")[0].rstrip().splitlines()[-1].strip()
        assert excerpt == "a" * 300 + "b" * (SUMMARY_CHARS - 300)
        assert "lead story" in prompt and "only source" in prompt
