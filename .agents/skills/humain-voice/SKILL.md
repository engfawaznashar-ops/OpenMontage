---
name: humain-voice
description: Generate Arabic or English narration with HUMAIN Voice preset voices (model Nebula) through the `humain_tts` provider or `tools/audio/humain_voice_cli.py`. Use when the user picks a HUMAIN voice, asks to compare HUMAIN voices, or says «اقرأ هذا السكربت بصوت mul_…».
---

# HUMAIN Voice TTS (preset voices only)

Source of truth: https://docs.voice.humain.com/en/sdk/python, /en/models, /en/authentication,
/en/api-guides/errors-and-rate-limits (SDK `humain-voice==0.18.1`).

## Setup
- The SDK lives in an isolated venv `.venv-humain`, never in the main `.venv`.
- Key: Fawaz pastes it into `HUMAIN_API_KEY.env` in the repo root (git-ignored by `*.env`). The CLI reads `HUMAIN_VOICE_API_KEY` from the environment first, then that file, then Windows Credential Manager (`test --remember`).
- Fawaz is not a programmer, so never ask him to run PowerShell. Run the commands yourself.
- Never ask for the key in chat, print it, or put it in code, logs or Git (the key file is git-ignored). `humain_voice_cli.py` scrubs it from errors.
- URL: `HUMAIN_VOICE_API_URL`, default `https://api.voice.humain.com`. A key works only on the environment that issued it.

## Commands (PowerShell, from the repo root)
```powershell
.\.venv-humain\Scripts\python.exe tools\audio\humain_voice_cli.py voices
.\.venv-humain\Scripts\python.exe tools\audio\humain_voice_cli.py say --text-file script.txt --voice mul_nourah --out outputs\voice\script.wav
.\.venv-humain\Scripts\python.exe tools\audio\humain_voice_cli.py test          # reuses outputs/humain-test if already generated
```
In a pipeline, call the registry tool `humain_tts` with `{text, voice_id, output_path}`; `tts_selector` discovers it.

## Voices and dialects
- `list_voices()` returns `id`, `label`, and `profile.speaker.{gender,dialect}`, `profile.languages`, and optionally `emotions`/`styles`.
- Dialect tags are BCP-47:
  - `ars` Saudi Arabic
  - `apc` Levantine
  - `arz` Egyptian
  - `ary` Moroccan
- The platform documents only `ars` (Saudi). It does **not** tag Hijazi, Najdi or Jeddawi, so never describe a HUMAIN voice that way unless the voice list says so.
- For «مكتب سند» (all characters must be Hijazi) a HUMAIN voice needs an ear test before use.
- IDs differ per environment, so look them up; don't hard-code them.
- Language is chosen from the text itself: any Arabic letter makes the voice speak Arabic.

## Emotion and speaking style (tags in the text)
- Put a tag before the words. It applies until the next tag: `[happy] يا جماعة! [whisper] لحظة…`.
- `<emotion value="sad">…</emotion>` wraps a span instead.
- Only these 12 work (tag names stay in English even in Arabic text):

| Tag | Use for | Tag | Use for |
|---|---|---|---|
| `[neutral]` | calm statement, decision | `[news]` | report, announcement |
| `[happy]` | excitement, laughter, hook energy | `[storytelling]` | narration, "once upon a time" |
| `[surprised]` | discovery, "wait… what?" | `[advertising]` | call to action, promo punch |
| `[sad]` | disappointment, sigh | `[call_center]` | polite service tone |
| `[angry]` | frustration | `[conversational]` | explaining, chatting to camera |
| `[fearful]` | alarm, urgency | | |
| `[whisper]` | secret, suspense, cliffhanger | | |

- **Any other `[name]` is SPOKEN aloud** (e.g. `[laughs]`). `say` handles this automatically:
  - It translates ElevenLabs-style tags (`EL_MAP`), e.g. excited/laughs → happy, amazed/gasps → surprised, whispers → whisper, sighs → sad, nervous/urgent → fearful, curious/confident → conversational.
  - It drops unknown tags, and drops tags the voice doesn't list.
- Each part has one delivery (an emotion or a style). The last tag wins. Tags aren't billed and don't count toward the limit.
- With `voice_references` (cloning), tags are skipped.
- **Options:**
  - `say --emotion <tag>` sets the starting delivery.
  - `--per-line` sends one request per line with a 300 ms pause, which suits multi-line scripts.
  - Tags carry over automatically when a script is split into chunks.
- No laughter or breath sounds (unlike ElevenLabs v4). Emotion comes through intonation and pace only.
- Reference read: `outputs/humain/sanad_ep01_script_tagged.txt` → `sanad_ep01_nourah_emotions.wav` (42 s vs 33 s untagged).

## Output and limits
- Raw PCM16 little-endian mono at `tts.get_sample_rate(model)` (24,000 Hz for Nebula). The CLI wraps it as WAV.
- 1,500 code points per request, and 25 s synthesis per request. `say` splits scripts into ≤600-character sentence chunks joined with 120 ms of silence.
- Always pass `timeout_seconds` (the CLI uses 30 s per chunk).

## Errors (branch on `code`, never on the message)

| Exit | Kind | Codes / signal | Action |
|---|---|---|---|
| 2 | auth | `AUTH_UNAUTHORIZED` / 401 on connect | Key missing, mistyped, revoked, or from another environment |
| 3 | permission | `AUTH_FORBIDDEN` / 403 | Check the key's scopes in the console |
| 4 | connection | network or timeout | Retry later; check `HUMAIN_VOICE_API_URL` |
| 5 | billing | `CREDITS_EXHAUSTED` (402), `BILLING_AUTHORIZATION_UNAVAILABLE` | **Stop.** Never buy credit or subscribe automatically; tell the user |
| 6 | api | `TTS_VOICE_NOT_FOUND`, `CHARACTER_COUNT_EXCEEDED`, `TTS_INPUT_NOT_ALLOWED`, … | Fix the input |

- TTS limits are checked before any charge.
- Realtime and TTS `429`s consume no credit.

## Not in scope
- Voice cloning with `voice_references`: no uploaded recordings.
