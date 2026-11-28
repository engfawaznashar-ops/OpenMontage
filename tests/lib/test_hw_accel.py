"""Quick Sync rewrite, CPU fallback and Remotion --gl handling (lib/hw_accel.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import hw_accel  # noqa: E402
from lib.hw_accel import rewrite_x264_to_qsv, with_remotion_gl  # noqa: E402
from tools.base_tool import BaseTool, ToolResult  # noqa: E402


def _opt(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_rewrites_crf_preset_and_pix_fmt():
    cmd = ["ffmpeg", "-y", "-i", "in.mp4", "-c:v", "libx264", "-preset", "fast",
           "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "out.mp4"]
    out = rewrite_x264_to_qsv(cmd)
    assert out is not None
    assert _opt(out, "-c:v") == "h264_qsv"
    assert _opt(out, "-preset") == "fast"
    assert _opt(out, "-global_quality") == "15"  # crf 18 - 3
    assert _opt(out, "-pix_fmt") == "nv12"
    assert "-crf" not in out
    assert out[-1] == "out.mp4" and _opt(out, "-c:a") == "aac"


def test_input_side_options_are_untouched():
    # -pix_fmt before -i describes raw input frames; it must not change.
    cmd = ["ffmpeg", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "64x64", "-i", "-",
           "-c:v", "libx264", "-crf", "20", "out.mp4"]
    out = rewrite_x264_to_qsv(cmd)
    assert out[:9] == cmd[:9]
    assert out[out.index("-c:v") + 1] == "h264_qsv"


def test_adds_quality_and_pix_fmt_when_missing():
    out = rewrite_x264_to_qsv(["ffmpeg", "-i", "a.mov", "-vcodec", "libx264", "-tune", "film", "b.mp4"])
    assert _opt(out, "-global_quality") == "20"  # libx264 default CRF 23 - 3
    assert _opt(out, "-pix_fmt") == "nv12"
    assert "-tune" not in out and "film" not in out


@pytest.mark.parametrize("cmd", [
    ["ffprobe", "-i", "a.mp4"],
    ["ffmpeg", "-i", "a.mp4", "-c:v", "copy", "b.mp4"],
    ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-crf", "0", "b.mp4"],          # lossless
    ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-pix_fmt", "yuv444p", "b.mp4"],  # 4:4:4
    ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-pix_fmt", "yuvj420p", "b.mp4"],  # full range
    ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-b:v", "4M", "b.mp4"],         # bitrate mode
    ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-profile:v", "high444", "b.mp4"],
])
def test_leaves_untranslatable_commands_alone(cmd):
    assert rewrite_x264_to_qsv(cmd) is None


def test_windows_exe_path_counts_as_ffmpeg():
    out = rewrite_x264_to_qsv([r"C:\ffmpeg\bin\ffmpeg.exe", "-i", "a", "-c:v", "libx264", "b.mp4"])
    assert out is not None


def test_hw_variant_respects_off_switch(monkeypatch):
    cmd = ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "b.mp4"]
    monkeypatch.setenv(hw_accel.HW_ENCODE_ENV, "off")
    assert hw_accel.hw_variant(cmd) is None
    monkeypatch.setenv(hw_accel.HW_ENCODE_ENV, "qsv")
    assert hw_accel.hw_variant(cmd) is not None


def test_auto_mode_skips_qsv_when_probe_fails(monkeypatch):
    monkeypatch.setenv(hw_accel.HW_ENCODE_ENV, "auto")
    monkeypatch.setattr(hw_accel, "qsv_available", lambda ffmpeg="ffmpeg": False)
    assert hw_accel.hw_variant(["ffmpeg", "-i", "a", "-c:v", "libx264", "b.mp4"]) is None


def test_remotion_gets_gl_angle(monkeypatch):
    monkeypatch.delenv(hw_accel.REMOTION_GL_ENV, raising=False)
    cmd = ["npx", "remotion", "render", "src/index.ts", "Main", "out.mp4"]
    assert with_remotion_gl(cmd) == [*cmd, "--gl=angle"]
    assert with_remotion_gl([r"C:\nodejs\npx.cmd", "remotion", "still", "e", "C", "o.png"])[-1] == "--gl=angle"
    explicit = [*cmd, "--gl=swangle"]
    assert with_remotion_gl(explicit) == explicit
    assert with_remotion_gl(["npx", "remotion", "compositions", "e"]) == ["npx", "remotion", "compositions", "e"]
    monkeypatch.setenv(hw_accel.REMOTION_GL_ENV, "off")
    assert with_remotion_gl(cmd) == cmd


class _Tool(BaseTool):
    name = "hw_test_tool"

    def execute(self, inputs: dict) -> ToolResult:
        return ToolResult(success=True)


def test_run_command_falls_back_to_libx264(monkeypatch, tmp_path):
    monkeypatch.setenv(hw_accel.HW_ENCODE_ENV, "qsv")
    out_file = tmp_path / "out.mp4"
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "h264_qsv" in cmd:
            out_file.write_bytes(b"partial")  # a failed QSV run leaves a stub
            raise subprocess.CalledProcessError(1, cmd, stderr="qsv init failed")
        assert not out_file.exists(), "partial QSV output must be removed before the fallback"
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("tools.base_tool.subprocess.run", fake_run)
    _Tool().run_command(["ffmpeg", "-i", "in.mp4", "-c:v", "libx264", "-crf", "18", str(out_file)])
    assert "h264_qsv" in calls[0]
    assert "libx264" in calls[1] and "-crf" in calls[1]


def test_run_command_uses_qsv_when_it_works(monkeypatch, tmp_path):
    monkeypatch.setenv(hw_accel.HW_ENCODE_ENV, "qsv")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("tools.base_tool.subprocess.run", fake_run)
    _Tool().run_command(["ffmpeg", "-i", "in.mp4", "-c:v", "libx264", str(tmp_path / "o.mp4")])
    assert len(calls) == 1 and "h264_qsv" in calls[0]
