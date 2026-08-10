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

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Instagram Reels: 9:16, 1080x1920, H.264 + AAC in mp4.
REEL_W, REEL_H = 1080, 1920
DEFAULT_DURATION = 8


AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}


def pick_music(music_dir: Path, seed: str) -> Path | None:
    """Choose a track deterministically from a directory, or None if empty.

    Seeded by date so retries within a day reuse the same track and the
    rotation is predictable. Absent or empty directory means a silent Reel,
    which still publishes.
    """
    if not music_dir.is_dir():
        log.info("no music directory at %s — Reel will be silent", music_dir)
        return None
    tracks = sorted(
        p for p in music_dir.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES
    )
    if not tracks:
        log.info("no audio files in %s — Reel will be silent", music_dir)
        return None
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(tracks)
    chosen = tracks[idx]
    log.info("using track %s", chosen.name)
    return chosen


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


def build_slideshow_command(
    slides: list[Path],
    out_path: Path,
    *,
    audio_path: Path | None = None,
    seconds_per_slide: float = 4.0,
    fps: int = 30,
) -> list[str]:
    """ffmpeg argv for a Reel that steps through several slides with audio.

    This is how a post carries both multiple stories and sound: Instagram
    allows audio on video only, never on a carousel, so the carousel's slides
    are sequenced into a Reel instead of being posted as separate images.

    Each slide gets its own gentle zoom so the video never looks like a static
    JPEG someone uploaded by mistake.
    """
    per = max(1.0, seconds_per_slide)
    frames = int(per * fps)

    cmd: list[str] = ["ffmpeg", "-y"]
    for slide in slides:
        cmd += ["-loop", "1", "-t", f"{per}", "-i", str(slide)]
    if audio_path is not None:
        cmd += ["-i", str(audio_path)]

    parts = []
    for i in range(len(slides)):
        parts.append(
            f"[{i}:v]scale={REEL_W * 2}:{REEL_H * 2},"
            f"zoompan=z='min(1.06,1+0.06*on/{frames})'"
            f":d={frames}:s={REEL_W}x{REEL_H}:fps={fps},setsar=1[v{i}]"
        )
    concat_inputs = "".join(f"[v{i}]" for i in range(len(slides)))
    parts.append(f"{concat_inputs}concat=n={len(slides)}:v=1:a=0,format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]

    total = per * len(slides)
    if audio_path is not None:
        cmd += [
            "-map", f"{len(slides)}:a",
            "-c:a", "aac", "-b:a", "160k",
            # Fade out rather than cut, and never let a long track extend the video.
            "-af", f"afade=t=out:st={max(total - 1.5, 0)}:d=1.5",
            "-shortest",
        ]
    else:
        cmd += ["-an"]

    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-r", str(fps), "-movflags", "+faststart", str(out_path),
    ]
    return cmd


def build_slideshow(
    slides: list[Path],
    out_path: Path,
    *,
    audio_path: Path | None = None,
    seconds_per_slide: float = 4.0,
) -> Path:
    """Encode the multi-slide Reel. Raises FFmpegMissing without ffmpeg."""
    if not slides:
        raise ValueError("no slides to encode")
    if not ffmpeg_available():
        raise FFmpegMissing(
            "ffmpeg is required to build Reels. GitHub Actions runners have it "
            "preinstalled; locally, install it or set POST_FORMAT=carousel."
        )
    if audio_path is not None and not audio_path.exists():
        log.warning("audio file %s not found — encoding silently", audio_path)
        audio_path = None

    cmd = build_slideshow_command(
        slides, out_path, audio_path=audio_path, seconds_per_slide=seconds_per_slide
    )
    log.info("encoding %d-slide reel%s", len(slides), " with audio" if audio_path else "")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg failed:\n%s", proc.stderr[-2000:])
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")
    log.info("reel written to %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path


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
