"""Shared fixtures for the TriAgent test suite."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from triagent.config import Settings, DEFAULT_FEEDS


@pytest.fixture()
def env_no_env_file(monkeypatch):
    """Ensure no .env file is loaded so Settings.from_env() reads only os.environ."""
    # Remove the dotenv file if it exists
    monkeypatch.delenv("PYTHONSTARTUP", raising=False)
    # Clear any env vars the config reads
    for key in (
        "ANTHROPIC_API_KEY",
        "IG_USER_ID",
        "IG_ACCESS_TOKEN",
        "PUBLIC_IMAGE_BASE_URL",
        "BRAND_HANDLE",
        "BRAND_NAME",
        "AFFILIATE_URLS",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture()
def settings(env_no_env_file):
    """Minimal valid Settings for tests that need one."""
    return Settings(
        anthropic_api_key="test-key",
        ig_user_id="123",
        ig_access_token="tok_123",
        public_image_base_url="https://example.com",
        brand_handle="@testbrand",
        brand_name="TestBrand",
    )


@pytest.fixture()
def sample_news_items():
    """A handful of NewsItem objects for testing."""
    base = dt.datetime(2026, 5, 16, 12, 0, tzinfo=dt.timezone.utc)
    return [
        type(
            "NewsItem",
            (),
            {
                "title": "Kemenz Wins Ironman Kona",
                "summary": "A thrilling finish as Kemenz edges out the competition.",
                "url": "https://example.com/kemenz-kona",
                "source": "Triathlete",
                "published": base - dt.timedelta(hours=2),
                "age_hours": lambda now=None: 2.0,
            },
        )(),
        type(
            "NewsItem",
            (),
            {
                "title": "New Tri-Suit Technology Debuted",
                "summary": "Speedo unveils next-gen wetsuit with improved buoyancy.",
                "url": "https://example.com/speedo-wetsuit",
                "source": "Slowtwitch",
                "published": base - dt.timedelta(hours=10),
                "age_hours": lambda now=None: 10.0,
            },
        )(),
        type(
            "NewsItem",
            (),
            {
                "title": "ITU World Championship Dates Announced",
                "summary": "Next year's series will feature a new Middle Eastern leg.",
                "url": "https://example.com/itu-2027",
                "source": "Tri247",
                "published": base - dt.timedelta(hours=20),
                "age_hours": lambda now=None: 20.0,
            },
        )(),
    ]


@pytest.fixture()
def sample_brief():
    """A valid DailyBrief for image rendering tests."""
    from triagent.summarizer import DailyBrief, Headline

    return DailyBrief(
        hook="Kemenz takes Kona in a photo finish",
        hook_emphasis="Kemenz",
        image_query="triathlete running out of surf at dawn",
        headlines=[
            Headline(
                title="Kemenz Wins Ironman Kona in Dramatic Swim Exit",
                one_liner="The Australian dominated from the start.",
                source="Triathlete",
                source_url="https://example.com/1",
            ),
            Headline(
                title="New Tri-Suit Tech Could Change Race Dynamics",
                one_liner="Speedo's wetsuit promises 3% faster swim splits.",
                source="Slowtwitch",
                source_url="https://example.com/2",
            ),
            Headline(
                title="ITU Adds Middle East Stop to 2027 Calendar",
                one_liner="Dubai will host the season opener next year.",
                source="Tri247",
                source_url="https://example.com/3",
            ),
        ],
        caption_body="What a day at Kona! Kemenz showed why they're the favorite. "
        "Also keeping an eye on Speedo's new wetsuit — could change the game.",
        engagement_prompt="Who wins Kona this year?",
        hashtags=[
            "triathlon",
            "ironman",
            "swimbikerun",
            "kona",
            "70point3",
            "agegroupathlete",
        ],
    )


@pytest.fixture()
def tmp_assets_dir(tmp_path):
    """A temporary directory mimicking assets/ for image tests."""
    assets = tmp_path / "assets"
    assets.mkdir()
    return assets