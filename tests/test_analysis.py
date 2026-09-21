from __future__ import annotations

import builtins
import json
import sys
import types
import wave
from pathlib import Path

import pytest
from PIL import Image

from replay_studio import analysis
from replay_studio.media import OperationCancelled


def test_no_audio_is_explicit_and_does_not_invent_transcript(tmp_path: Path) -> None:
    result = analysis.transcribe(None, tmp_path)
    assert result["status"] == "complete"
    assert result["backend"] == "none"
    assert result["segments"] == []
    assert "no audio" in result["warning"]
    assert json.loads((tmp_path / "asr.json").read_text())["segments"] == []


def test_missing_asr_dependency_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 16000)
    original = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "faster_whisper":
            raise ImportError("intentionally absent in this unit test")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.setattr(analysis, "_release_models", lambda: None)
    result = analysis.transcribe(audio, tmp_path / "asr")
    assert result["status"] == "unavailable"
    assert result["segments"] == []
    assert "no output was invented" in result["warning"]


def test_cancel_cannot_be_converted_to_unavailable(tmp_path: Path) -> None:
    with pytest.raises(OperationCancelled):
        analysis.transcribe(None, tmp_path, cancel=lambda: True)


def test_frames_are_ordered_and_do_not_escape_base(tmp_path: Path) -> None:
    Image.new("RGB", (100, 100)).save(tmp_path / "frame.jpg")
    assert analysis.checked_frames([{"time": 0, "path": "frame.jpg"}], tmp_path)
    for frames in (
        [{"time": 0, "path": "../outside.jpg"}],
        [{"time": 1, "path": "frame.jpg"}, {"time": 0, "path": "frame.jpg"}],
        [{"time": float("nan"), "path": "frame.jpg"}],
    ):
        with pytest.raises(ValueError):
            analysis.checked_frames(frames, tmp_path)


def test_no_visual_frames_never_produces_fake_index(tmp_path: Path) -> None:
    result = analysis.visual([], tmp_path, tmp_path / "visual")
    assert result["status"] == "unavailable" and result["index"] is None
    assert not (tmp_path / "visual" / "visual.npz").exists()


def test_optional_vlm_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPLAY_VLM_MODEL", raising=False)
    assert analysis._vlm_descriptions([])["status"] == "disabled"


@pytest.mark.parametrize("device", ["cuda", "cuda:0", "mps", "invalid"])
def test_synchronous_planner_rejects_unleased_accelerator_before_loading(
    monkeypatch: pytest.MonkeyPatch, device: str
) -> None:
    monkeypatch.setenv("REPLAY_PLANNER_MODEL", "unit-test-model-must-not-load")
    monkeypatch.setenv("REPLAY_PLANNER_DEVICE", device)
    with pytest.raises(ValueError, match="only supports CPU"):
        analysis.select_evidence("Find the error", [{"id": "safe"}], 10)


@pytest.mark.parametrize("local", [False, True])
def test_asr_revision_is_resolved_before_model_load_or_rejected_for_local_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local: bool
) -> None:
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 16000)
    pinned = tmp_path / "snapshots" / "resolved-commit"
    calls = []
    backend = types.ModuleType("faster_whisper")
    utils = types.ModuleType("faster_whisper.utils")

    def download(name: str, *, revision: str | None) -> str:
        calls.append(("download", name, revision))
        return str(pinned)

    def load(path: str, **kwargs: object) -> object:
        calls.append(("load", path))
        return types.SimpleNamespace(
            transcribe=lambda *a, **kw: (iter([]), types.SimpleNamespace(language="en"))
        )

    backend.WhisperModel = load
    utils.download_model = download
    monkeypatch.setitem(sys.modules, "faster_whisper", backend)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    monkeypatch.setenv("REPLAY_ASR_MODEL", str(tmp_path) if local else "base")
    monkeypatch.setenv("REPLAY_ASR_REVISION", "requested-commit")
    monkeypatch.setenv("REPLAY_MODEL_DEVICE", "cpu")
    monkeypatch.setattr(analysis, "_release_models", lambda: None)
    result = analysis.transcribe(audio, tmp_path / "asr")
    if local:
        assert result["status"] == "unavailable"
        assert calls == []
    else:
        assert result["status"] == "complete"
        assert calls == [("download", "base", "requested-commit"), ("load", str(pinned))]
        assert result["requested_revision"] == "requested-commit"
        assert result["model_revision"] == "resolved-commit"
