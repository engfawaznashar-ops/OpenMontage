"""HUMAIN Voice text-to-speech (preset platform voices, model Nebula).

The official SDK (`humain-voice==0.18.1`) lives in the isolated venv `.venv-humain`, so this provider
shells out to `tools/audio/humain_voice_cli.py` with that interpreter instead of importing the SDK.
Voice cloning (`voice_references`) is intentionally not exposed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.base_tool import (
    BaseTool,
    Determinism,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)
from tools.provider_pricing import PriceQuoteRequired

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = Path(__file__).resolve().with_name("humain_voice_cli.py")


def humain_python() -> Path:
    sub = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    return REPO_ROOT / ".venv-humain" / sub


def _key_file_has_key() -> bool:
    from tools.audio.humain_voice_cli import key_from_file

    return bool(key_from_file())


EXIT_KINDS = {2: "auth", 3: "permission", 4: "connection", 5: "billing", 6: "api"}


class HumainTTS(BaseTool):
    name = "humain_tts"
    version = "0.1.0"
    provider = "humain"
    capability = "tts"
    tier = ToolTier.VOICE
    runtime = ToolRuntime.API
    stability = ToolStability.EXPERIMENTAL
    determinism = Determinism.STOCHASTIC
    dependencies = ["env:HUMAIN_VOICE_API_KEY"]
    install_instructions = (
        "Create the isolated venv and install the SDK: "
        "py -3.12 -m venv .venv-humain; .venv-humain\\Scripts\\python.exe -m pip install humain-voice==0.18.1 keyring. "
        "Then paste the key (console.voice.humain.com > API keys) into HUMAIN_API_KEY.env in the repo root, "
        "or set HUMAIN_VOICE_API_KEY."
    )
    agent_skills = ["humain-voice"]
    capabilities = ["text_to_speech"]
    supports = {"voice_cloning": False, "ssml": False, "languages": ["ar", "en"], "output": "wav_pcm16_mono_24k",
                "emotion_tags": ["neutral", "happy", "sad", "angry", "fearful", "surprised", "whisper"],
                "style_tags": ["news", "storytelling", "advertising", "call_center", "conversational"]}
    best_for = ["Arabic narration with HUMAIN preset voices (Saudi ars, Levantine, Egyptian, Moroccan)"]
    not_good_for = ["voice cloning (not enabled here)", "free-form ElevenLabs v4 tags like [laughs] (mapped to 12 deliveries)"]
    resource_profile = ResourceProfile(network_required=True)
    retry_policy = RetryPolicy(max_retries=0)
    side_effects = ["calls the HUMAIN Voice API (uses account credit)", "writes a WAV file"]
    idempotency_key_fields = ["text", "voice_id", "model"]
    input_schema = {
        "type": "object",
        "required": ["text", "voice_id"],
        "properties": {
            "text": {"type": "string", "minLength": 1},
            "voice_id": {"type": "string", "minLength": 1, "description": "Voice id (UUID) or label, e.g. mul_nourah"},
            "model": {"type": "string", "enum": ["nebula"], "default": "nebula"},
            "max_chars": {"type": "integer", "minimum": 50, "maximum": 1500, "default": 600},
            "emotion": {
                "type": "string",
                "enum": ["neutral", "happy", "sad", "angry", "fearful", "surprised", "whisper",
                         "news", "storytelling", "advertising", "call_center", "conversational"],
                "description": "Starting delivery. Switch inside the text with tags like [happy] … [whisper] …",
            },
            "per_line": {"type": "boolean", "default": False, "description": "One request per line with a pause (scripts)"},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        py = humain_python()
        if not py.is_file():
            return ToolStatus.UNAVAILABLE
        if os.getenv("HUMAIN_VOICE_API_KEY") or _key_file_has_key():
            return ToolStatus.AVAILABLE
        try:  # key saved in Windows Credential Manager by `test --remember`
            r = subprocess.run([str(py), str(CLI), "has-key"], capture_output=True, timeout=20)
            return ToolStatus.AVAILABLE if r.returncode == 0 else ToolStatus.UNAVAILABLE
        except Exception:
            return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs):
        raise PriceQuoteRequired("HUMAIN Voice pricing depends on the account plan; check the console Usage page")

    def execute(self, inputs) -> ToolResult:
        try:
            from jsonschema import validate

            validate(inputs, self.input_schema)
            out = Path(inputs.get("output_path") or "humain_tts.wav")
            if out.suffix.lower() != ".wav":
                raise ValueError("HUMAIN output_path must end in .wav")
            py = humain_python()
            if not py.is_file():
                raise RuntimeError(self.install_instructions)
            with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
                f.write(inputs["text"])
                text_file = f.name
            try:
                cmd = [str(py), str(CLI), "say", "--text-file", text_file, "--voice", inputs["voice_id"],
                       "--out", str(out), "--max-chars", str(inputs.get("max_chars", 600))]
                if inputs.get("emotion"):
                    cmd += ["--emotion", inputs["emotion"]]
                if inputs.get("per_line"):
                    cmd.append("--per-line")
                env = {**os.environ, "PYTHONUTF8": "1"}
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=900)
            finally:
                Path(text_file).unlink(missing_ok=True)
            if r.returncode != 0:
                kind = EXIT_KINDS.get(r.returncode, "error")
                return ToolResult(success=False, error=f"humain_{kind}: {(r.stderr or r.stdout).strip()[-800:]}")
            data = json.loads(r.stdout)
            return ToolResult(
                success=True,
                data={"provider": self.provider, "model": "nebula", "output": data["output"], "format": "wav",
                      "voice": data["voice"], "voice_id": data["voice_id"], "duration_s": data["duration_s"],
                      "sample_rate": data["sample_rate"], "cost_status": "unquoted"},
                artifacts=[data["output"]],
                cost_usd=None,
                model="nebula",
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
