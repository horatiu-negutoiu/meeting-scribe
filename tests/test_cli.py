from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

import pytest

from meeting_scribe.cli import DEFAULT_MODEL, _output_path, main
from meeting_scribe.workflow import ProcessingRequest, WorkflowError


def test_installed_console_entry_point_shows_help() -> None:
    """Keep the package's advertised command wired to the CLI module."""
    executable = Path(sys.executable).parent / (
        "meeting-scribe.exe" if os.name == "nt" else "meeting-scribe"
    )

    result = subprocess.run(
        [str(executable), "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "Create a transcript and summary from a meeting recording." in result.stdout


def _audio_file(tmp_path: Path, suffix: str = ".m4a") -> Path:
    audio = tmp_path / f"meeting{suffix}"
    audio.write_bytes(b"audio")
    return audio


def test_valid_m4a_reaches_workflow_with_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    created_at = datetime(2026, 7, 24, 12, 34, 56).astimezone()
    monkeypatch.setattr("meeting_scribe.cli._local_time", lambda: created_at)
    captured: list[ProcessingRequest] = []

    assert main([str(_audio_file(tmp_path))], workflow_runner=captured.append) == 0

    request = captured[0]
    assert request.audio_file == (tmp_path / "meeting.m4a").resolve()
    assert request.output_file == (tmp_path / "transcription-output-20260724-123456.md").resolve()
    assert request.language == "en"
    assert request.speakers is True
    assert request.model == DEFAULT_MODEL
    assert request.created_at == created_at
    assert (
        capsys.readouterr().out
        == f"Created meeting transcript: {request.output_file}\n"
    )


@pytest.mark.parametrize("suffix", [".txt", ".aac", ""])
def test_unsupported_audio_never_reaches_workflow(
    tmp_path: Path, suffix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    called = False

    def runner(_: ProcessingRequest) -> None:
        nonlocal called
        called = True

    with pytest.raises(SystemExit, match="2"):
        main([str(_audio_file(tmp_path, suffix))], workflow_runner=runner)
    assert called is False


def test_missing_path_never_reaches_workflow(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        main([str(tmp_path / "missing.m4a")])


def test_output_names_use_numeric_suffixes_for_same_second_collisions(tmp_path: Path) -> None:
    audio = _audio_file(tmp_path)
    created_at = datetime(2026, 7, 24, 12, 34, 56).astimezone()
    (tmp_path / "transcription-output-20260724-123456.md").write_text("first")
    (tmp_path / "transcription-output-20260724-123456-1.md").write_text("second")

    assert _output_path(audio, None, created_at) == tmp_path / "transcription-output-20260724-123456-2.md"


def test_options_are_forwarded_to_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    created_at = datetime(2026, 7, 24, 12, 34, 56).astimezone()
    monkeypatch.setattr("meeting_scribe.cli._local_time", lambda: created_at)
    captured: list[ProcessingRequest] = []

    assert (
        main(
            [
                str(_audio_file(tmp_path)),
                "--output-dir",
                str(tmp_path / "generated"),
                "--language",
                "fr",
                "--no-speakers",
                "--model",
                "custom-model",
            ],
            workflow_runner=captured.append,
        )
        == 0
    )
    request = captured[0]
    assert request.output_file == (
        tmp_path / "generated" / "transcription-output-20260724-123456.md"
    ).resolve()
    assert request.language == "fr"
    assert request.speakers is False
    assert request.model == "custom-model"


def test_missing_api_key_is_actionable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert main([str(_audio_file(tmp_path))]) == 1

    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


def test_workflow_and_api_errors_are_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fail(_: ProcessingRequest) -> None:
        raise WorkflowError("transcription request was rejected")

    assert main([str(_audio_file(tmp_path))], workflow_runner=fail) == 1
    assert "transcription request was rejected" in capsys.readouterr().err
