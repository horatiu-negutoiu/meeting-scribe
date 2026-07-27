from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from meeting_scribe.workflow import (
    AudioChunk,
    CHUNK_OVERLAP_SECONDS,
    DEFAULT_DIARIZATION_MODEL,
    MAX_TRANSCRIPTION_UPLOAD_BYTES,
    ProcessingRequest,
    SummaryError,
    TranscriptionError,
    Transcript,
    TranscriptSegment,
    WorkflowError,
    _combine_chunk_transcripts,
    render_transcript,
    run_workflow,
    summarize,
    transcribe,
    validate_summary,
    write_report_atomically,
)


class FakeTranscriptions:
    def __init__(self, response: object | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response: object | Exception) -> None:
        self.audio = type("Audio", (), {"transcriptions": FakeTranscriptions(response)})()


class FakeResponses:
    def __init__(self, responses: list[object | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeSummaryClient:
    def __init__(self, responses: list[object | Exception]) -> None:
        self.responses = FakeResponses(responses)


def _valid_summary() -> str:
    return """## Executive summary
The team agreed to ship.
## Decisions
### Confirmed decisions
- Ship the release.
### Proposals and unresolved items
- Consider a later follow-up.
## Rationale/context
The release was ready.
## Action items
- Alex will publish it. Evidence: \"I will publish it\".
## Open questions
- None recorded.
## Risks/disagreements
- No risks recorded.
## Discussion notes
- The team reviewed the release.
## Evidence
- [12.00s] \"I will publish it\".
"""


def _request(tmp_path: Path, *, model: str = DEFAULT_DIARIZATION_MODEL, speakers: bool = True) -> ProcessingRequest:
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"audio")
    return ProcessingRequest(
        audio,
        tmp_path / "meeting.md",
        "en",
        speakers,
        model,
        datetime(2026, 7, 24, 12, 34, 56).astimezone(),
    )


def test_diarized_response_uses_anonymous_labels_and_timestamps(tmp_path: Path) -> None:
    client = FakeClient(
        {"text": "Hello. Hi.", "segments": [
            {"speaker": "speaker_0", "start": 0, "end": 1.2, "text": "Hello."},
            {"speaker": "speaker_1", "start": 1.3, "end": 2.0, "text": "Hi."},
            {"speaker": "speaker_0", "start": 2.1, "end": 3.0, "text": "Again."},
        ]}
    )

    transcript = transcribe(_request(tmp_path), client)

    assert [segment.speaker for segment in transcript.segments] == ["Speaker 1", "Speaker 2", "Speaker 1"]
    rendered = render_transcript(transcript)
    assert "Speaker 1 [0.00s]" in rendered
    assert "speaker_0" not in rendered
    call = client.audio.transcriptions.calls[0]
    assert Path(call["file"].name).name == "meeting.m4a"
    assert {key: value for key, value in call.items() if key != "file"} == {
        "model": DEFAULT_DIARIZATION_MODEL,
        "language": "en",
        "response_format": "diarized_json",
        "chunking_strategy": "auto",
    }


def test_model_override_uses_non_diarized_format_and_normalizes_plain_text(tmp_path: Path) -> None:
    client = FakeClient({"text": "A plain transcript", "segments": None})

    transcript = transcribe(_request(tmp_path, model="custom-transcriber"), client)

    assert transcript.text == "A plain transcript"
    assert transcript.segments == ()
    call = client.audio.transcriptions.calls[0]
    assert call["response_format"] == "verbose_json"
    assert "chunking_strategy" not in call


def test_api_error_preserves_provider_details_for_personal_use(tmp_path: Path) -> None:
    with pytest.raises(TranscriptionError, match="Transcription request failed") as error:
        transcribe(_request(tmp_path), FakeClient(RuntimeError("sensitive API payload")))

    assert "sensitive API payload" in str(error.value)


def test_summary_error_preserves_provider_details_for_personal_use() -> None:
    with pytest.raises(SummaryError, match="provider response details") as error:
        summarize(Transcript("A short transcript."), FakeSummaryClient([
            RuntimeError("provider response details"),
        ]))  # type: ignore[arg-type]

    assert "provider response details" in str(error.value)


def test_oversized_recording_transcribes_ordered_chunks_with_offsets_and_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    request.audio_file.write_bytes(b"a" * (MAX_TRANSCRIPTION_UPLOAD_BYTES + 1))
    chunk_paths = [tmp_path / "chunk-0.m4a", tmp_path / "chunk-1.m4a"]
    for path in chunk_paths:
        path.write_bytes(b"audio")
    monkeypatch.setattr(
        "meeting_scribe.workflow.create_audio_chunks",
        lambda *_: (AudioChunk(chunk_paths[0], 0), AudioChunk(chunk_paths[1], 10)),
    )
    client = FakeClient({"text": "unused"})
    client.audio.transcriptions.response = [
        {"text": "Hello from the meeting", "segments": [
                {"speaker": "a", "start": 8, "end": 12, "text": "Hello from the meeting"},
        ]},
        {"text": "meeting continues now", "segments": [
            {"speaker": "a", "start": 0, "end": 1, "text": "meeting"},
            {"speaker": "a", "start": 1, "end": 3, "text": "continues now"},
        ]},
    ]
    def create(**kwargs: object) -> object:
        client.audio.transcriptions.calls.append(kwargs)
        return client.audio.transcriptions.response.pop(0)

    client.audio.transcriptions.create = create  # type: ignore[method-assign]

    transcript = transcribe(request, client)

    assert len(client.audio.transcriptions.calls) == 2
    assert transcript.text == "Hello from the meeting continues now"
    assert transcript.was_chunked is True
    assert transcript.chunk_count == 2
    assert [(segment.start, segment.end, segment.text) for segment in transcript.segments] == [
        (8.0, 12.0, "Hello from the meeting"),
        (11.0, 13.0, "continues now"),
    ]
    assert [segment.speaker for segment in transcript.segments] == ["Speaker 1", "Speaker 2"]


def test_duration_rejection_retries_small_diarized_recording_as_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    assert request.audio_file.stat().st_size <= MAX_TRANSCRIPTION_UPLOAD_BYTES
    chunk_paths = [tmp_path / "chunk-0.m4a", tmp_path / "chunk-1.m4a"]
    for path in chunk_paths:
        path.write_bytes(b"audio")
    monkeypatch.setattr(
        "meeting_scribe.workflow.create_audio_chunks",
        lambda *_: (AudioChunk(chunk_paths[0], 0), AudioChunk(chunk_paths[1], 598)),
    )
    client = FakeClient({"text": "unused"})
    responses: list[object | Exception] = [
        RuntimeError(
            "audio duration 1836.308 seconds is longer than 1400 seconds "
            "which is the maximum for this model"
        ),
        {"text": "First half"},
        {"text": "Second half"},
    ]

    def create(**kwargs: object) -> object:
        client.audio.transcriptions.calls.append(kwargs)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    client.audio.transcriptions.create = create  # type: ignore[method-assign]

    transcript = transcribe(request, client)

    assert len(client.audio.transcriptions.calls) == 3
    assert transcript.text == "First half Second half"
    assert transcript.was_chunked is True
    assert transcript.chunk_count == 2


def test_overlap_reconciliation_keeps_uncertain_boundary_speech() -> None:
    transcript = _combine_chunk_transcripts([
        (AudioChunk(Path("first.m4a"), 0), Transcript("alpha", (TranscriptSegment("alpha", start=0, end=5),))),
        (AudioChunk(Path("second.m4a"), 4), Transcript("beta", (TranscriptSegment("beta", start=0, end=2),))),
    ])

    assert [segment.text for segment in transcript.segments] == ["alpha", "beta"]


def test_oversized_recording_reports_missing_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    request.audio_file.write_bytes(b"a" * (MAX_TRANSCRIPTION_UPLOAD_BYTES + 1))
    monkeypatch.setattr("meeting_scribe.workflow.shutil.which", lambda _: None)

    with pytest.raises(TranscriptionError, match="ffmpeg"):
        transcribe(request, FakeClient({"text": "not used"}))


def test_audio_chunks_respect_the_model_duration_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        "meeting_scribe.workflow._require_ffmpeg", lambda: ("ffmpeg", "ffprobe"),
    )
    monkeypatch.setattr(
        "meeting_scribe.workflow._audio_duration", lambda *_: 3_126.3,
    )

    def create_chunk(args: list[str]) -> object:
        commands.append(args)
        Path(args[-1]).write_bytes(b"audio")
        return type("Completed", (), {"stdout": ""})()

    monkeypatch.setattr("meeting_scribe.workflow._run_media_command", create_chunk)

    from meeting_scribe.workflow import create_audio_chunks

    chunks = create_audio_chunks(tmp_path / "meeting.m4a", tmp_path)

    assert len(chunks) == 6
    assert all(float(command[command.index("-t") + 1]) <= 600 for command in commands)
    assert chunks[1].start == pytest.approx(600 - CHUNK_OVERLAP_SECONDS)


def test_summary_prompt_requires_evidence_and_supported_claims() -> None:
    client = FakeSummaryClient([{"output_text": _valid_summary()}])

    summary = summarize(Transcript("[1.00s] Alex: I will publish it."), client)  # type: ignore[arg-type]

    assert summary.markdown == _valid_summary()
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6"
    system_prompt = call["input"][0]["content"]  # type: ignore[index]
    assert "Do not invent decisions, owners, due dates" in system_prompt
    assert "Keep confirmed decisions separate" in system_prompt
    assert "timestamp or a short quoted transcript excerpt" in system_prompt


def test_summary_response_validation_rejects_missing_required_sections() -> None:
    with pytest.raises(SummaryError, match="missing required sections"):
        validate_summary("## Executive summary\nOnly this heading is present.")


def test_workflow_writes_summary_and_retains_source_transcript(tmp_path: Path) -> None:
    request = _request(tmp_path)
    client = FakeClient({"text": "We agreed to ship."})
    client.responses = FakeResponses([{"output_text": _valid_summary()}])

    run_workflow(request, client=client)  # type: ignore[arg-type]

    report = request.output_file.read_text()
    assert report.startswith("# Meeting summary\n\n## Artifact details")
    assert "- Source filename: `meeting.m4a`" in report
    assert "- Created: 2026-07-24T12:34:56" in report
    assert f"- Transcription model: `{DEFAULT_DIARIZATION_MODEL}`" in report
    assert "- Summary model: `gpt-5.6`" in report
    assert "- Chunking: Single upload; no chunking was required." in report
    assert "# Transcript\n\nWe agreed to ship." in report


def test_failed_atomic_write_leaves_no_partial_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "transcription-output-20260724-123456.md"

    def fail_replace(_: Path, __: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("meeting_scribe.workflow.os.replace", fail_replace)

    with pytest.raises(WorkflowError, match="Could not write transcription artifact"):
        write_report_atomically(destination, "complete report")

    assert not destination.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_long_summary_synthesizes_every_chunk_and_flags_uncertainty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("meeting_scribe.workflow.SUMMARY_CONTEXT_BUDGET_CHARS", 10)
    monkeypatch.setattr("meeting_scribe.workflow.SUMMARY_CHUNK_CHARS", 20)
    client = FakeSummaryClient([
        {"output_text": "Chunk one: proposed a launch."},
        {"output_text": "Chunk two: launch date is uncertain."},
        {"output_text": "Chunk three: no date was confirmed."},
        {"output_text": _valid_summary()},
    ])

    summary = summarize(Transcript("proposed launch date uncertain"), client)  # type: ignore[arg-type]

    assert summary.markdown == _valid_summary()
    assert len(client.responses.calls) == 4
    synthesis_prompt = client.responses.calls[-1]["input"][1]["content"]  # type: ignore[index]
    assert "Chunk one: proposed a launch." in synthesis_prompt
    assert "Chunk two: launch date is uncertain." in synthesis_prompt
    assert "Chunk three: no date was confirmed." in synthesis_prompt
    assert "explicitly flag any uncertainty" in synthesis_prompt
