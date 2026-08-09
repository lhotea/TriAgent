"""Render the daily card as an Instagram Reel.

Instagram will not attach audio to a static image post — only Reels carry
sound. So "add music" necessarily means publishing video instead of an image.

Two things worth knowing about audio here:

* The Graph API cannot reach Instagram's in-app music library. That catalogue
  is licensed for use inside the app only. Anything published through the API
  must have its audio baked into the file, which means you need rights to the
  track you supply.
* Because of that, a Reel published via the API carries no trending-audio
  signal, so it won't get the reach boost that picking a popular sound in the
  app would give it.

Audio is therefore opt-in: set REEL_AUDIO to a file you're licensed to use. No
audio configured means a silent Reel, which still posts fine.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Instagram Reels: 9:16, 1080x1920, H.264 + AAC in mp4.
REEL_W, REEL_H = 1080, 1920
DEFAULT_DURATION = 8


class FFmpegMissing(RuntimeError):
    """Raised when ffmpeg is not on PATH."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def build_reel_command(
    frame_path: Path,
    out_path: Path,
    *,
    audio_path: Path | None = None,
    duration: int = DEFAULT_DURATION,
    fps: int = 30,
) -> list[str]:
    """Build the ffmpeg argv for a slow-zoom Reel from a single still.

    Split out from execution so the command can be asserted in tests without
    needing ffmpeg present.

    The zoom is deliberately gentle (1.0 -> 1.08). A static image posted as
    video reads as lazy; a slow push gives it enough motion to look intentional
    without becoming a distraction.
    """
    total_frames = duration * fps
    # zoompan operates per output frame; 'z' ramps across the clip. The scale
    # up front is what keeps the zoom from stepping visibly — zoompan samples
    # from the upscaled source rather than the 1080-wide original.
    vf = (
        f"scale={REEL_W * 4}:{REEL_H * 4},"
        f"zoompan=z='min(1.08,1+0.08*on/{total_frames})'"
        f":d={total_frames}:s={REEL_W}x{REEL_H}:fps={fps},"
        f"format=yuv420p"
    )

    cmd: list[str] = ["ffmpeg", "-y", "-loop", "1", "-t", str(duration), "-i", str(frame_path)]

    if audio_path is not None:
        cmd += ["-i", str(audio_path)]

    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-r", str(fps)]

    if audio_path is not None:
        # -shortest guards against a track longer than the clip; af afade
        # avoids the abrupt cut that an unfaded trim would leave.
        cmd += [
            "-c:a", "aac", "-b:a", "128k",
            "-af", f"afade=t=out:st={max(duration - 1, 0)}:d=1",
            "-shortest",
        ]
    else:
        cmd += ["-an"]

    cmd += ["-movflags", "+faststart", str(out_path)]
    return cmd


def build_reel(
    frame_path: Path,
    out_path: Path,
    *,
    audio_path: Path | None = None,
    duration: int = DEFAULT_DURATION,
) -> Path:
    """Encode the Reel. Raises FFmpegMissing when ffmpeg isn't installed."""
    if not ffmpeg_available():
        raise FFmpegMissing(
            "ffmpeg is required to build Reels. GitHub Actions runners have it "
            "preinstalled; locally, install it or set POST_FORMAT=image."
        )
    if audio_path is not None and not audio_path.exists():
        log.warning("audio file %s not found — encoding a silent Reel", audio_path)
        audio_path = None

    cmd = build_reel_command(
        frame_path, out_path, audio_path=audio_path, duration=duration
    )
    log.info("encoding reel: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # ffmpeg puts everything on stderr, including the actual error.
        log.error("ffmpeg failed:\n%s", proc.stderr[-2000:])
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")
    log.info("reel written to %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path
