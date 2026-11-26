"""Re-encodes videos so the Cozyla frame's decoder plays them (docs/cozyla-frame.md)."""

import subprocess
from pathlib import Path


def convert(src: Path, max_px: int) -> Path:
    """H.264 8-bit fitted within max_px (never upscaled), sides cropped to multiples of 32."""
    dest = src.with_suffix(".conv.mp4")
    vf = (f"scale=w=min(iw\\,iw*{max_px}/max(iw\\,ih)):h=-2,crop=trunc(iw/32)*32:trunc(ih/32)*32,"
          "format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709")
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-profile:v", "main",
                        "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", str(dest)],
                       check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg isn't installed")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg failed: {' '.join(e.stderr.strip().splitlines()[-1:]) or e}")
    return dest
