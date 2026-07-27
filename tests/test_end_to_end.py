"""No-network end-to-end coverage for the command's normal path."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from meeting_scribe.cli import main
from meeting_scribe.workflow import ProcessingRequest, run_workflow


class _FakeTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return {
            "text": "We agreed to publish the release on Friday.",
            "segments": [
                {
                    "speaker": "speaker_0",
                    "start": 12.0,
                    "end": 15.0,
                    "text": "We agreed to publish the release on Friday.",
                }
            ],
        }


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return {"output_text": """## Executive summary
The release will be published on Friday.
## Decisions
### Confirmed decisions
- Publish the release on Friday.
### Proposals and unresolved items
- None recorded.
## Rationale/context
- The team agreed to the release timing.
## Action items
- Publish the release on Friday. Evidence: \"publish the release on Friday\".
## Open questions
- None recorded.
## Risks/disagreements
- None recorded.
## Discussion notes
- The release timing was confirmed.
## Evidence
- [12.00s] \"We agreed to publish the release on Friday.\"
"""}


class _FakeClient:
    def __init__(self) -> None:
        transcriptions = _FakeTranscriptions()
        self.audio = type("Audio", (), {"transcriptions": transcriptions})()
        self.responses = _FakeResponses()


def test_command_writes_a_complete_artifact_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    audio_file = tmp_path / "meeting.m4a"
    audio_file.write_bytes(b"sample audio")
    created_at = datetime(2026, 7, 24, 12, 34, 56).astimezone()
    client = _FakeClient()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("meeting_scribe.cli._local_time", lambda: created_at)

    def run_with_fake_client(request: ProcessingRequest) -> None:
        run_workflow(request, client=client)  # type: ignore[arg-type]

    assert main([str(audio_file)], workflow_runner=run_with_fake_client) == 0

    output_file = tmp_path / "transcription-output-20260724-123456.md"
    report = output_file.read_text(encoding="utf-8")
    assert capsys.readouterr().out == f"Created meeting transcript: {output_file}\n"
    assert "- Source filename: `meeting.m4a`" in report
    assert "- Created: 2026-07-24T12:34:56" in report
    assert "## Executive summary" in report
    assert "## Evidence" in report
    assert "## Speaker 1 [12.00s]" in report
    assert len(client.audio.transcriptions.calls) == 1
    assert len(client.responses.calls) == 1
