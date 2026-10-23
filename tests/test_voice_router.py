"""Tests for local Whisper /api/v1/voice/transcribe & /status (Epic #2591, #2593)."""

from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from identity import Principal  # noqa: E402
from routers import voice as voice_router  # noqa: E402


def _make_pcm16_wav(*, duration_s: float = 0.5, amplitude: int = 0, rate: int = 16000) -> bytes:
    frames = int(duration_s * rate)
    sample = int(amplitude).to_bytes(2, byteorder="little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(sample * frames)
    return buf.getvalue()


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    fake_principal = Principal(id="test-operator", type="human", name="Operator", scopes=["*"])
    app.dependency_overrides[voice_router._VOICE_READ] = lambda: fake_principal
    app.dependency_overrides[voice_router._VOICE_CHAT] = lambda: fake_principal
    app.include_router(voice_router.router)
    yield TestClient(app)
    voice_router.set_voice_backend_for_testing(None)


def test_voice_status_and_unavailable_fallback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        voice_router,
        "detect_voice_capability",
        lambda: {"available": False, "engine": "none", "model": "base.en", "detail": "missing whisper"},
    )
    status_res = client.get("/api/v1/voice/status")
    assert status_res.status_code == 200
    assert status_res.headers.get("cache-control") == "no-store"
    assert status_res.json()["available"] is False

    non_silent = _make_pcm16_wav(duration_s=0.2, amplitude=1200)
    tx_res = client.post("/api/v1/voice/transcribe", content=non_silent, headers={"Content-Type": "audio/wav"})
    assert tx_res.status_code == 503
    assert tx_res.json()["detail"]["code"] == "voice_unavailable"


def test_silent_wav_returns_empty_and_suppresses_hallucinations(client: TestClient) -> None:
    silent_wav = _make_pcm16_wav(duration_s=0.5, amplitude=0)
    res = client.post("/api/v1/voice/transcribe", content=silent_wav, headers={"Content-Type": "audio/wav"})
    assert res.status_code == 200
    assert res.json()["text"] == ""
    assert res.headers.get("cache-control") == "no-store"

    # Also verify hallucination phrase suppression on voiced clip
    voice_router.set_voice_backend_for_testing(
        lambda _p: voice_router.VoiceEngineResult("Thanks for watching!", 0.4, 0.1, "faster-whisper")
    )
    voiced = _make_pcm16_wav(duration_s=0.4, amplitude=2000)
    res2 = client.post("/api/v1/voice/transcribe", content=voiced, headers={"Content-Type": "audio/wav"})
    assert res2.status_code == 200
    assert res2.json()["text"] == ""


def test_transcribe_success_and_temp_file_scrubbing(client: TestClient) -> None:
    observed_modes: list[int] = []
    observed_paths: list[Path] = []

    def _fake_backend(temp_path: Path) -> voice_router.VoiceEngineResult:
        observed_paths.append(temp_path)
        observed_modes.append(temp_path.stat().st_mode & 0o777)
        return voice_router.VoiceEngineResult("run gasification sensitivity sweep", 1.25, 0.02)

    voice_router.set_voice_backend_for_testing(_fake_backend)
    voiced = _make_pcm16_wav(duration_s=0.4, amplitude=2500)
    res = client.post(
        "/api/v1/voice/transcribe",
        content=voiced,
        headers={"Content-Type": "audio/webm;codecs=opus"},
    )
    assert res.status_code == 200
    assert res.json()["text"] == "run gasification sensitivity sweep"
    assert observed_modes == [0o600]
    assert observed_paths and not observed_paths[0].exists()


def test_mime_size_and_duration_validation(client: TestClient) -> None:
    bad_mime = client.post("/api/v1/voice/transcribe", content=b"123", headers={"Content-Type": "text/plain"})
    assert bad_mime.status_code == 415

    empty = client.post("/api/v1/voice/transcribe", content=b"", headers={"Content-Type": "audio/webm"})
    assert empty.status_code == 400

    too_long = _make_pcm16_wav(duration_s=61.0, amplitude=500, rate=1000)
    long_res = client.post("/api/v1/voice/transcribe", content=too_long, headers={"Content-Type": "audio/wav"})
    assert long_res.status_code == 413
