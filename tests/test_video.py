"""Tests for triagent.video — Reel encoding.

ffmpeg isn't guaranteed locally, so the command construction is asserted
directly; the actual encode is exercised in CI where ffmpeg is preinstalled.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from triagent.video import (
    DEFAULT_DURATION,
    REEL_H,
    REEL_W,
    FFmpegMissing,
    build_reel,
    build_reel_command,
)


class TestBuildReelCommand:
    def test_outputs_reel_dimensions(self):
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"))
        joined = " ".join(cmd)
        assert f"s={REEL_W}x{REEL_H}" in joined

    def test_silent_when_no_audio(self):
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"))
        assert "-an" in cmd
        assert "aac" not in cmd

    def test_includes_audio_when_given(self):
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"), audio_path=Path("a.mp3"))
        assert "a.mp3" in cmd
        assert "aac" in cmd
        assert "-an" not in cmd

    def test_audio_is_trimmed_to_clip(self):
        """A track longer than the clip must not stretch the video."""
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"), audio_path=Path("a.mp3"))
        assert "-shortest" in cmd

    def test_audio_fades_out(self):
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"), audio_path=Path("a.mp3"))
        assert any("afade" in c for c in cmd)

    def test_faststart_for_streaming(self):
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"))
        assert "+faststart" in cmd

    def test_h264_codec(self):
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"))
        assert "libx264" in cmd

    def test_duration_is_honoured(self):
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"), duration=12)
        assert "12" in cmd

    def test_default_duration_used(self):
        cmd = build_reel_command(Path("f.png"), Path("o.mp4"))
        assert str(DEFAULT_DURATION) in cmd

    def test_overwrites_existing_output(self):
        assert "-y" in build_reel_command(Path("f.png"), Path("o.mp4"))


class TestBuildReel:
    def test_raises_when_ffmpeg_absent(self, tmp_path):
        with patch("triagent.video.ffmpeg_available", return_value=False):
            with pytest.raises(FFmpegMissing, match="ffmpeg is required"):
                build_reel(tmp_path / "f.png", tmp_path / "o.mp4")

    def test_missing_audio_degrades_to_silent(self, tmp_path):
        """A misconfigured audio path shouldn't kill the whole run."""
        out = tmp_path / "o.mp4"
        out.write_bytes(b"x")
        proc = MagicMock(returncode=0, stderr="")
        with (
            patch("triagent.video.ffmpeg_available", return_value=True),
            patch("triagent.video.subprocess.run", return_value=proc) as run,
        ):
            build_reel(tmp_path / "f.png", out, audio_path=tmp_path / "nope.mp3")
        assert "-an" in run.call_args[0][0]

    def test_raises_on_ffmpeg_failure(self, tmp_path):
        proc = MagicMock(returncode=1, stderr="boom")
        with (
            patch("triagent.video.ffmpeg_available", return_value=True),
            patch("triagent.video.subprocess.run", return_value=proc),
        ):
            with pytest.raises(RuntimeError, match="ffmpeg exited 1"):
                build_reel(tmp_path / "f.png", tmp_path / "o.mp4")
