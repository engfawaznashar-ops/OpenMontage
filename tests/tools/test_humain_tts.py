"""Offline tests for the HUMAIN Voice provider and CLI (no network, no SDK, no key)."""

import json
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.audio import humain_tts, humain_voice_cli as cli


def test_split_text_respects_limit_and_sentences():
    text = "جملة أولى قصيرة. " * 40 + "جملة بدون نهاية " * 60
    parts = cli.split_text(text, 200)
    assert all(len(p) <= 200 for p in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


@pytest.mark.parametrize(
    "msg, code, kind",
    [
        ("Connection refused 401 Unauthorized", None, "auth"),
        ("forbidden 403", None, "permission"),
        ("failed", "CREDITS_EXHAUSTED", "billing"),
        ("Cannot connect to host api.voice.humain.com", None, "connection"),
        ("bad voice", "TTS_VOICE_NOT_FOUND", "api"),
        ("request 2f66da2d-4010-4401-b5bf-3c1b7a985de3 failed", None, "error"),  # a UUID containing 401 is not auth
    ],
)
def test_classify(msg, code, kind):
    assert cli.classify(RuntimeError(msg), None, code).kind == kind


def test_scrub_hides_key():
    key = "hv_secret_123456"
    assert key not in cli.scrub(f"boom {key} x-api-key: {key}", key)


def _row(label, dialect, gender):
    return {"label": label, "id": label + "-id", "languages": ["ar", "en"], "dialect": dialect,
            "dialect_meaning": "", "gender": gender, "emotions": None, "styles": None}


def test_pick_two_prefers_two_saudi_genders():
    rows = [_row("mul_fadi", "apc", "male"), _row("mul_abdullah", "ars", "male"),
            _row("mul_nourah", "ars", "female"), _row("mul_sara", "ars", "female")]
    chosen, _ = cli.pick_two(rows)
    assert {c["label"] for c in chosen} == {"mul_nourah", "mul_abdullah"}


def test_pick_two_falls_back_to_other_arabic():
    chosen, note = cli.pick_two([_row("mul_youssef", "arz", "male"), _row("mul_nourah", "ars", "female")])
    assert [c["label"] for c in chosen] == ["mul_nourah", "mul_youssef"] and "عربي" in note


def test_cached_samples_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "TEST_DIR", tmp_path)
    with wave.open(str(tmp_path / "mul_nourah.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\x01\x00" * 24000)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "text_sha": cli.text_sha(cli.TEST_TEXT),
        "samples": [{"file": "mul_nourah.wav", "label": "mul_nourah", "sample_rate": 24000}]}), encoding="utf-8")
    data = cli.cached_samples(cli.TEST_TEXT)
    assert data and data["samples"][0]["duration_s"] == 1.0
    assert cli.cached_samples("نص مختلف") is None


def test_cli_without_key_exits_2(monkeypatch, tmp_path):
    monkeypatch.delenv("HUMAIN_VOICE_API_KEY", raising=False)
    monkeypatch.setattr(cli, "get_key", lambda: None)
    f = tmp_path / "s.txt"
    f.write_text("مرحبا", encoding="utf-8")
    assert cli.main(["say", "--text-file", str(f), "--voice", "mul_nourah", "--out", str(tmp_path / "o.wav")]) == 2


def test_provider_registered_shape():
    tool = humain_tts.HumainTTS()
    assert tool.capability == "tts" and tool.provider == "humain"
    assert "voice_references" not in tool.input_schema["properties"]


def test_provider_maps_billing_exit(monkeypatch, tmp_path):
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("")
    monkeypatch.setattr(humain_tts, "humain_python", lambda: fake_py)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=5, stdout="", stderr="CREDITS_EXHAUSTED"))
    r = humain_tts.HumainTTS().execute({"text": "مرحبا", "voice_id": "mul_nourah", "output_path": str(tmp_path / "a.wav")})
    assert not r.success and r.error.startswith("humain_billing")


def test_provider_rejects_non_wav(tmp_path):
    r = humain_tts.HumainTTS().execute({"text": "مرحبا", "voice_id": "x", "output_path": str(tmp_path / "a.mp3")})
    assert not r.success and ".wav" in r.error


def test_key_file_formats(tmp_path):
    f = tmp_path / "k.env"
    nl = chr(10)
    f.write_text(nl.join(["# put the key below", "HUMAIN_VOICE_API_KEY=", ""]), encoding="utf-8")
    assert cli.key_from_file(f) is None
    f.write_text(nl.join(["# x", "HUMAIN_VOICE_API_KEY= 'hv_abcdefghijklmnop' ", ""]), encoding="utf-8")
    assert cli.key_from_file(f) == "hv_abcdefghijklmnop"
    f.write_text(nl.join(["# x", "HUMAIN_VOICE_API_KEY=", "hv_abcdefghijklmnop1234", ""]), encoding="utf-8")
    assert cli.key_from_file(f) == "hv_abcdefghijklmnop1234"


def test_normalize_tags_maps_keeps_and_drops():
    text, notes = cli.normalize_tags("[excited] يا جماعة! [Whisper] لحظة [laughter] [sad][angry] خلاص")
    assert text.startswith("[happy] يا جماعة! [whisper] لحظة")
    assert "laughter" not in text and "[sad]" not in text and "[angry] خلاص" in text
    assert any("laughter" in n for n in notes)


def test_normalize_tags_respects_voice_support():
    text, _ = cli.normalize_tags("[happy] أهلاً", supported=set())
    assert "[" not in text


def test_carry_tags_opens_each_chunk_with_active_delivery():
    chunks = cli.carry_tags(["[happy] أول.", "ثاني.", "[whisper] ثالث.", "رابع."], start=None)
    assert chunks == ["[happy] أول.", "[happy] ثاني.", "[whisper] ثالث.", "[whisper] رابع."]
    assert cli.carry_tags(["أهلاً"], start="news") == ["[news] أهلاً"]
