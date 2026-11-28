"""Hardware acceleration for local renders: Intel Quick Sync and Remotion's GPU.

OpenMontage tools build their ffmpeg encodes with ``-c:v libx264``, which runs on
the CPU. On a machine with an Intel GPU the same encode can run on Quick Sync
(``h264_qsv``). On an Arc iGPU (Core Ultra 9 185H, 2026-11-28, a real 1080x1920
episode clip) it was about 3x faster at the same SSIM, with files 7-45% larger,
and it leaves the CPU free for rendering.

``BaseTool.run_command`` calls :func:`hw_variant` to get the Quick Sync form of a
libx264 command. When the Quick Sync run fails, it runs the original libx264
command, so a missing or broken GPU never fails a render.

It also calls :func:`with_remotion_gl`, which adds ``--gl=angle`` to
``remotion render`` / ``remotion still`` so Chrome paints frames on the GPU.

Environment switches:

``OPENMONTAGE_HW_ENCODE``  ``auto`` (default) | ``qsv`` | ``off``
    auto: use Quick Sync when a short probe encode works on this machine.
    qsv:  skip the probe and always try Quick Sync first.
    off:  never rewrite; libx264 stays.

``OPENMONTAGE_REMOTION_GL``  ``angle`` (default) | any Remotion ``--gl`` value | ``off``
    Added as ``--gl=<value>`` when the command does not pass ``--gl`` itself.
"""

from __future__ import annotations

import functools
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

HW_ENCODE_ENV = "OPENMONTAGE_HW_ENCODE"
REMOTION_GL_ENV = "OPENMONTAGE_REMOTION_GL"

_OFF_VALUES = {"off", "0", "false", "no", "none", "cpu"}

# Quick Sync quality for a given libx264 CRF. Measured 2026-11-28 on a real
# 1080x1920 episode clip (SSIM in RGB against the source):
#   libx264 crf 18: 0.9861, 15.6 MB  ->  h264_qsv gq 15: 0.9862, 22.5 MB
#   libx264 crf 23: 0.9812,  7.0 MB  ->  h264_qsv gq 20: 0.9816,  7.5 MB
# So global_quality = crf - 3 keeps the quality; Quick Sync spends more bits.
QSV_QUALITY_OFFSET = -3
_X264_DEFAULT_CRF = 23

_VIDEO_CODEC_FLAGS = {"-c:v", "-vcodec", "-codec:v"}
_PIX_FMT_FLAGS = {"-pix_fmt", "-pix_fmt:v"}
_PROFILE_FLAGS = {"-profile:v", "-vprofile"}
_QSV_PROFILES = {"baseline", "main", "high"}

_X264_TO_QSV_PRESET = {
    "ultrafast": "veryfast",
    "superfast": "veryfast",
    "veryfast": "veryfast",
    "faster": "faster",
    "fast": "fast",
    "medium": "medium",
    "slow": "slow",
    "slower": "slower",
    "veryslow": "veryslow",
    "placebo": "veryslow",
}

# libx264-only options. h264_qsv has no equivalent, so they are dropped.
_X264_ONLY_OPTIONS = {"-tune", "-x264-params", "-x264opts"}

# Rate-control options this module does not translate. A command that uses
# one keeps libx264 so its output stays exactly as its author intended.
_UNTRANSLATED_OPTIONS = {"-qp", "-q:v", "-qscale:v", "-b:v", "-maxrate", "-bufsize", "-pass"}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip().lower() or default


def _is_ffmpeg(cmd: list[str]) -> bool:
    return bool(cmd) and Path(str(cmd[0])).stem.lower() == "ffmpeg"


def _output_section_start(args: list[str]) -> int:
    """Index of the first token after the last ``-i <input>`` pair.

    ffmpeg applies an option to the next file named after it. Options before an
    ``-i`` belong to that input (``-pix_fmt yuv420p -i -`` describes raw input
    frames), so only tokens after the last input are output options.
    """
    start = 1
    for i, token in enumerate(args[:-1]):
        if token == "-i":
            start = i + 2
    return start


def rewrite_x264_to_qsv(cmd: list[str]) -> Optional[list[str]]:
    """Return the Quick Sync (``h264_qsv``) form of a libx264 ffmpeg command.

    Returns None when the command is not an ffmpeg libx264 encode, or when it
    asks for something this rewrite does not translate (lossless, 4:4:4 or
    10-bit output, bitrate or two-pass rate control, a High 4:4:4 profile).
    """
    if not _is_ffmpeg(cmd):
        return None
    args = [str(token) for token in cmd]
    start = _output_section_start(args)
    head, tail = args[:start], args[start:]

    out: list[str] = []
    codec_positions: list[int] = []
    saw_crf = False
    saw_pix_fmt = False
    i = 0
    while i < len(tail):
        token = tail[i]
        value = tail[i + 1] if i + 1 < len(tail) else None

        if token in _VIDEO_CODEC_FLAGS and value == "libx264":
            out += [token, "h264_qsv"]
            codec_positions.append(len(out))
            i += 2
            continue
        if token in _UNTRANSLATED_OPTIONS:
            return None
        if token == "-crf" and value is not None:
            try:
                crf = float(value)
            except ValueError:
                return None
            if crf <= 0:  # CRF 0 is lossless; Quick Sync has no lossless H.264.
                return None
            quality = min(51, max(1, round(crf) + QSV_QUALITY_OFFSET))
            out += ["-global_quality", str(quality)]
            saw_crf = True
            i += 2
            continue
        if token == "-preset" and value is not None:
            out += ["-preset", _X264_TO_QSV_PRESET.get(value.lower(), "medium")]
            i += 2
            continue
        if token in _PIX_FMT_FLAGS and value is not None:
            if value != "yuv420p":  # yuvj420p (full range), 4:2:2, 4:4:4, 10-bit
                return None
            out += [token, "nv12"]
            saw_pix_fmt = True
            i += 2
            continue
        if token in _PROFILE_FLAGS and value is not None:
            if value.lower() not in _QSV_PROFILES:
                return None
            out += [token, value]
            i += 2
            continue
        if token in _X264_ONLY_OPTIONS and value is not None:
            i += 2
            continue
        out.append(token)
        i += 1

    if not codec_positions:
        return None

    # libx264 picks CRF 23 and, for RGB input, 4:4:4 when none is given. Spell
    # out the matching quality and the widely playable 4:2:0 layout instead.
    extra: list[str] = []
    if not saw_crf:
        extra += ["-global_quality", str(_X264_DEFAULT_CRF + QSV_QUALITY_OFFSET)]
    if not saw_pix_fmt:
        extra += ["-pix_fmt", "nv12"]
    for position in reversed(codec_positions):
        out[position:position] = extra
    return head + out


@functools.lru_cache(maxsize=None)
def qsv_available(ffmpeg: str = "ffmpeg") -> bool:
    """True when this ffmpeg can encode H.264 on Quick Sync on this machine.

    Runs a 6-frame encode to a null output once per ffmpeg binary, so a build
    without QSV, a machine without an Intel GPU and a broken driver all read
    as unavailable.
    """
    probe = [
        ffmpeg, "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=0.2",
        "-c:v", "h264_qsv", "-pix_fmt", "nv12", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(probe, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("Quick Sync probe could not run (%s); encoding stays on the CPU", exc)
        return False
    if proc.returncode != 0:
        log.info("Quick Sync unavailable; encoding stays on the CPU: %s", (proc.stderr or "").strip()[:300])
        return False
    return True


def hw_variant(cmd: list[str]) -> Optional[list[str]]:
    """The Quick Sync form of ``cmd`` to try first, or None to run ``cmd`` as is."""
    mode = _env(HW_ENCODE_ENV, "auto")
    if mode in _OFF_VALUES:
        return None
    qsv_cmd = rewrite_x264_to_qsv(cmd)
    if qsv_cmd is None:
        return None
    if mode != "qsv" and not qsv_available(str(cmd[0])):
        return None
    return qsv_cmd


def output_target(cmd: list[str]) -> Optional[Path]:
    """The file an ffmpeg command writes (its last argument), when it is a file."""
    if not cmd:
        return None
    last = str(cmd[-1])
    if last.startswith("-") or last.upper() in {"NUL", "/DEV/NULL"} or "://" in last:
        return None
    return Path(last)


def _is_remotion_frame_command(cmd: list[str]) -> bool:
    tokens = [str(token) for token in cmd]
    for i, token in enumerate(tokens[:-1]):
        if Path(token).stem.lower() == "remotion" and tokens[i + 1] in {"render", "still"}:
            return True
    return False


def with_remotion_gl(cmd: list[str]) -> list[str]:
    """Add ``--gl=angle`` (or ``$OPENMONTAGE_REMOTION_GL``) to a Remotion render.

    Without ``--gl``, Remotion lets headless Chrome choose, and Chrome paints
    frames in software. Commands that already pass ``--gl`` are left alone.
    """
    gl = _env(REMOTION_GL_ENV, "angle")
    if gl in _OFF_VALUES or not _is_remotion_frame_command(cmd):
        return cmd
    if any(str(token).startswith("--gl") for token in cmd):
        return cmd
    return [*cmd, f"--gl={gl}"]
