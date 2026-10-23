# ruff: noqa: B008
"""Local Whisper voice-to-text router (Epic #2591, #2593, Panel th_8820201930a3)."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import shutil
import struct
import subprocess
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from identity import Principal, require_scope

logger = logging.getLogger("dashboard.voice")
router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
_VOICE_READ = require_scope("staff.read")
_VOICE_CHAT = require_scope("staff.chat")

ALLOWED_AUDIO_MIMES: frozenset[str] = frozenset(
    {"audio/webm", "audio/wav", "audio/x-wav", "audio/ogg", "audio/mp4", "audio/mpeg"}
)
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_DURATION_S = 60.0
NO_SPEECH_PROB_THRESHOLD = 0.65
TEMP_PREFIX = "rd-voice-"
HALLUCINATION_PHRASES: frozenset[str] = frozenset(
    {
        "you",
        "thank you",
        "thank you.",
        "thanks for watching",
        "thanks for watching.",
        "thank you for watching",
        "thank you for watching.",
        "subtitles by the amara.org community",
        "[blank_audio]",
    }
)

_MODEL_SEMAPHORE = asyncio.Semaphore(1)
_CACHED_MODEL: Any = None
_CACHED_MODEL_NAME: str | None = None
_TEST_BACKEND_OVERRIDE: Callable[[Path], VoiceEngineResult] | None = None


@dataclass(frozen=True)
class VoiceEngineResult:
    text: str
    duration_s: float
    no_speech_prob: float = 0.0
    engine: str = "faster-whisper"


def set_voice_backend_for_testing(fn: Callable[[Path], VoiceEngineResult] | None) -> None:
    global _TEST_BACKEND_OVERRIDE
    _TEST_BACKEND_OVERRIDE = fn


def _temp_dir() -> Path:
    shm = Path("/dev/shm")
    return shm if shm.is_dir() and os.access(shm, os.W_OK) else Path("/tmp")


def sweep_stale_voice_temp_files() -> int:
    removed = 0
    for base in {Path("/dev/shm"), Path("/tmp")}:
        if not base.is_dir():
            continue
        for candidate in base.glob(f"{TEMP_PREFIX}*.tmp"):
            try:
                candidate.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


sweep_stale_voice_temp_files()


def _normalize_mime(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _inspect_wav(payload: bytes) -> tuple[float | None, bool]:
    if len(payload) < 44 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
        return None, False
    try:
        with wave.open(io.BytesIO(payload), "rb") as wf:
            frames, rate, width, channels = (
                wf.getnframes(),
                wf.getframerate(),
                wf.getsampwidth(),
                wf.getnchannels(),
            )
            duration_s = (frames / float(rate)) if rate > 0 else 0.0
            if frames == 0:
                return duration_s, True
            raw = wf.readframes(min(frames, rate * 10))
            if width == 2 and len(raw) >= 2:
                samples = struct.unpack(f"<{len(raw) // 2}h", raw[: (len(raw) // 2) * 2])
                peak = max((abs(s) for s in samples), default=0)
                return duration_s, (peak < 32 and channels >= 1)
            return duration_s, all(b == 0 for b in raw)
    except (wave.Error, EOFError, struct.error):
        return None, False


def _filter_hallucination(text: str, no_speech_prob: float) -> str:
    cleaned = text.strip()
    if not cleaned or no_speech_prob >= NO_SPEECH_PROB_THRESHOLD:
        return ""
    return "" if cleaned.lower().rstrip(".!") in HALLUCINATION_PHRASES else cleaned


def detect_voice_capability() -> dict[str, Any]:
    model_name = os.environ.get("RD_WHISPER_MODEL", "base.en")
    if _TEST_BACKEND_OVERRIDE is not None:
        return {"available": True, "engine": "test-override", "model": model_name, "detail": "ready"}
    if importlib.util.find_spec("faster_whisper") is not None:
        if shutil.which("ffmpeg") is None:
            return {"available": False, "engine": "faster-whisper", "model": model_name, "detail": "ffmpeg missing"}
        return {"available": True, "engine": "faster-whisper", "model": model_name, "detail": "ready"}
    for binary in ("whisper-cli", "whisper-cpp", "whisper"):
        if shutil.which(binary):
            return {"available": True, "engine": binary, "model": model_name, "detail": "ready"}
    return {
        "available": False,
        "engine": "none",
        "model": model_name,
        "detail": "No local Whisper engine (faster-whisper or whisper-cli) found.",
    }


def _run_local_whisper_sync(temp_path: Path) -> VoiceEngineResult:
    if _TEST_BACKEND_OVERRIDE is not None:
        return _TEST_BACKEND_OVERRIDE(temp_path)
    cap = detect_voice_capability()
    if not cap["available"]:
        raise RuntimeError(cap["detail"])
    model_name = cap["model"]
    if cap["engine"] == "faster-whisper":
        global _CACHED_MODEL, _CACHED_MODEL_NAME
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        if _CACHED_MODEL is None or _CACHED_MODEL_NAME != model_name:
            _CACHED_MODEL = WhisperModel(model_name, device="cpu", compute_type="int8")
            _CACHED_MODEL_NAME = model_name
        segments, info = _CACHED_MODEL.transcribe(str(temp_path), beam_size=1, vad_filter=True)
        seg_list = list(segments)
        text = " ".join(s.text.strip() for s in seg_list if s.text.strip())
        avg_nsp = sum(getattr(s, "no_speech_prob", 0.0) for s in seg_list) / len(seg_list) if seg_list else 1.0
        return VoiceEngineResult(text, float(getattr(info, "duration", 0.0) or 0.0), avg_nsp)
    proc = subprocess.run(
        [cap["engine"], "-f", str(temp_path), "-nt"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{cap['engine']} exited with code {proc.returncode}")
    return VoiceEngineResult(proc.stdout.strip(), 0.0, 0.0, cap["engine"])


@router.get("/status")
async def get_voice_status(response: Response, _peer: Principal = Depends(_VOICE_READ)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {
        **detect_voice_capability(),
        "max_bytes": MAX_AUDIO_BYTES,
        "max_duration_s": MAX_AUDIO_DURATION_S,
        "allowed_mimes": sorted(ALLOWED_AUDIO_MIMES),
    }


@router.post("/transcribe")
async def transcribe_voice(
    request: Request, response: Response, _peer: Principal = Depends(_VOICE_CHAT)
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    mime = _normalize_mime(request.headers.get("content-type"))
    if mime not in ALLOWED_AUDIO_MIMES:
        raise HTTPException(415, {"code": "unsupported_media_type", "detail": f"Unsupported MIME: {mime}"})
    payload = await request.body()
    if not payload:
        raise HTTPException(400, {"code": "empty_audio", "detail": "Audio payload must not be empty."})
    if len(payload) > MAX_AUDIO_BYTES:
        raise HTTPException(413, {"code": "audio_too_large", "detail": "Exceeds 10MB limit."})

    wav_duration_s, is_silent_wav = _inspect_wav(payload)
    if wav_duration_s is not None and wav_duration_s > MAX_AUDIO_DURATION_S:
        raise HTTPException(413, {"code": "audio_too_long", "detail": f"WAV duration {wav_duration_s:.1f}s > 60s."})
    if is_silent_wav:
        return {"text": "", "duration_s": round(wav_duration_s or 0.0, 3), "engine": "vad-silence-gate"}

    cap = detect_voice_capability()
    if not cap["available"]:
        raise HTTPException(503, {"code": "voice_unavailable", "detail": cap["detail"]})

    temp_path = _temp_dir() / f"{TEMP_PREFIX}{uuid.uuid4().hex}.tmp"
    started = time.monotonic()
    try:
        fd = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        async with _MODEL_SEMAPHORE:
            result = await asyncio.to_thread(_run_local_whisper_sync, temp_path)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("voice.transcribe failed (%s)", type(exc).__name__)
        raise HTTPException(503, {"code": "voice_unavailable", "detail": str(exc)}) from exc
    finally:
        temp_path.unlink(missing_ok=True)

    if result.duration_s > MAX_AUDIO_DURATION_S:
        raise HTTPException(413, {"code": "audio_too_long", "detail": "Audio exceeds 60s."})
    text = _filter_hallucination(result.text, result.no_speech_prob)
    logger.info(
        "voice.transcribe ok bytes=%d ms=%d chars=%d", len(payload), int((time.monotonic() - started) * 1000), len(text)
    )
    return {"text": text, "duration_s": round(result.duration_s or (wav_duration_s or 0.0), 3), "engine": result.engine}
