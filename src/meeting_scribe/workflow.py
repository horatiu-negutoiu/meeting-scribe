"""Transcription boundary and safe, application-facing transcript model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory, mkstemp
from typing import Any, Protocol

from meeting_scribe.config import get_openai_client

LOGGER = logging.getLogger(__name__)

DEFAULT_DIARIZATION_MODEL = "gpt-4o-transcribe-diarize"
DEFAULT_SUMMARY_MODEL = "gpt-5.6"
# The Audio Transcriptions API accepts uploads up to 25 MB.  Chunks are kept
# well below that limit because a container's metadata makes exact byte budgets
# brittle across ffmpeg versions.
MAX_TRANSCRIPTION_UPLOAD_BYTES = 25 * 1024 * 1024
CHUNK_TARGET_BYTES = 20 * 1024 * 1024
CHUNK_AUDIO_BITRATE = 64_000
CHUNK_OVERLAP_SECONDS = 2.0
# The diarization model permits up to 1,400 seconds per request, but much
# shorter chunks give the API predictable turnaround and limit the work lost
# if one request times out. The overlap is included within this ceiling.
MAX_TRANSCRIPTION_CHUNK_SECONDS = 600.0
# This is an application-level input budget expressed in characters.  It keeps
# requests comfortably below a model context window without pretending that
# character count is an exact token count for every language.
SUMMARY_CONTEXT_BUDGET_CHARS = 80_000
SUMMARY_CHUNK_CHARS = 16_000

SUMMARY_SECTION_HEADINGS = (
    "## Executive summary",
    "## Decisions",
    "### Confirmed decisions",
    "### Proposals and unresolved items",
    "## Rationale/context",
    "## Action items",
    "## Open questions",
    "## Risks/disagreements",
    "## Discussion notes",
    "## Evidence",
)

SUMMARY_SYSTEM_PROMPT = """You create detailed, evidence-grounded Markdown meeting summaries.

Use only the supplied transcript. Do not invent decisions, owners, due dates,
commitments, context, or certainty. Keep confirmed decisions separate from
proposals and unresolved items. Mention an owner or date only when the
transcript explicitly supports it. For each substantive claim, include a
timestamp or a short quoted transcript excerpt in the Evidence section. If the
transcript is unclear, conflicting, incomplete, or lacks evidence, say so.

Return exactly these Markdown sections, in this order:
## Executive summary
## Decisions
### Confirmed decisions
### Proposals and unresolved items
## Rationale/context
## Action items
## Open questions
## Risks/disagreements
## Discussion notes
## Evidence
"""


class WorkflowError(RuntimeError):
    """An expected failure while processing a valid transcript request."""


class TranscriptionError(WorkflowError):
    """Raised when OpenAI cannot complete a transcription request."""


class SummaryError(WorkflowError):
    """Raised when a meeting summary cannot be safely generated."""


@dataclass(frozen=True, slots=True)
class ProcessingRequest:
    """All options needed by the audio-processing workflow."""

    audio_file: Path
    output_file: Path
    language: str
    speakers: bool
    model: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """A transcript turn, labelled only by an anonymous speaker identifier."""

    text: str
    speaker: str | None = None
    start: float | None = None
    end: float | None = None


@dataclass(frozen=True, slots=True)
class Transcript:
    """Normalized transcription response consumed by the rest of the app."""

    text: str
    segments: tuple[TranscriptSegment, ...] = ()
    chunk_count: int = 1
    was_chunked: bool = False


@dataclass(frozen=True, slots=True)
class MeetingSummary:
    """A validated Markdown summary grounded in one transcript."""

    markdown: str


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """A temporary, upload-safe audio file and its source-time offset."""

    path: Path
    start: float


class OpenAIClient(Protocol):
    """The small portion of the SDK used here, deliberately easy to fake."""

    audio: Any
    responses: Any


def _value(value: object, name: str, default: object = None) -> object:
    """Read an SDK object or a dict-shaped test fixture uniformly."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def normalize_transcription(response: object) -> Transcript:
    """Convert SDK responses into a stable model without identifying people.

    Diarization labels distinguish voices but are not identities. Every unique
    source label is therefore replaced with an anonymous ``Speaker N`` label.
    """
    text = str(_value(response, "text", "")).strip()
    source_segments = _value(response, "segments", ()) or ()
    labels: dict[str, str] = {}
    segments: list[TranscriptSegment] = []

    for source in source_segments:
        segment_text = str(_value(source, "text", "")).strip()
        if not segment_text:
            continue
        raw_speaker = _value(source, "speaker")
        speaker = None
        if raw_speaker is not None:
            key = str(raw_speaker)
            speaker = labels.setdefault(key, f"Speaker {len(labels) + 1}")
        segments.append(
            TranscriptSegment(
                text=segment_text,
                speaker=speaker,
                start=_optional_float(_value(source, "start")),
                end=_optional_float(_value(source, "end")),
            )
        )

    if not text and segments:
        text = " ".join(segment.text for segment in segments)
    return Transcript(text=text, segments=tuple(segments))


def _transcription_options(request: ProcessingRequest) -> dict[str, object]:
    use_diarization = request.speakers and request.model == DEFAULT_DIARIZATION_MODEL
    options: dict[str, object] = {
        "model": request.model,
        "language": request.language,
        "response_format": "diarized_json" if use_diarization else "verbose_json",
    }
    if use_diarization:
        options["chunking_strategy"] = "auto"
    return options


def _require_ffmpeg() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise TranscriptionError(
            "This recording must be split before transcription, but ffmpeg (including "
            "ffprobe) is unavailable. Install ffmpeg and run the command again."
        )
    return ffmpeg, ffprobe


def _run_media_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        LOGGER.exception("ffmpeg could not process the recording.")
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise TranscriptionError(
            "Could not split the recording with ffmpeg. Confirm that the input is a "
            f"readable audio file and try again. Details: {detail}"
        ) from error


def _audio_duration(audio_file: Path, ffprobe: str) -> float:
    completed = _run_media_command([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio_file),
    ])
    try:
        duration = float(completed.stdout.strip())
    except ValueError as error:
        raise TranscriptionError("ffprobe could not determine the recording duration.") from error
    if duration <= 0:
        raise TranscriptionError("The recording has no readable audio duration.")
    return duration


def create_audio_chunks(audio_file: Path, directory: Path) -> tuple[AudioChunk, ...]:
    """Re-encode a recording into bounded, overlapping M4A uploads.

    Re-encoding makes chunk size predictable even for variable-bitrate source
    files.  Each following chunk starts slightly before the prior one ends, so
    speech on a cut is submitted twice instead of being silently lost.
    """
    ffmpeg, ffprobe = _require_ffmpeg()
    duration = _audio_duration(audio_file, ffprobe)
    LOGGER.info("Preparing overlapping upload chunks for %.1f seconds of audio.", duration)
    core_duration = max(
        1.0,
        min(
            (CHUNK_TARGET_BYTES * 8 / CHUNK_AUDIO_BITRATE) - CHUNK_OVERLAP_SECONDS,
            MAX_TRANSCRIPTION_CHUNK_SECONDS - CHUNK_OVERLAP_SECONDS,
        ),
    )
    chunks: list[AudioChunk] = []
    start = 0.0
    index = 0
    while start < duration:
        length = min(core_duration + CHUNK_OVERLAP_SECONDS, duration - start)
        output = directory / f"chunk-{index:04d}.m4a"
        _run_media_command([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(audio_file),
            "-vn", "-c:a", "aac", "-b:a", str(CHUNK_AUDIO_BITRATE), str(output),
        ])
        if not output.is_file() or output.stat().st_size > MAX_TRANSCRIPTION_UPLOAD_BYTES:
            raise TranscriptionError(
                "ffmpeg produced a chunk too large for the transcription upload limit. "
                "Try a lower-bitrate source recording."
            )
        chunks.append(AudioChunk(path=output, start=start))
        start += core_duration
        index += 1
    LOGGER.info("Prepared %d upload chunks.", len(chunks))
    return tuple(chunks)


def _is_diarization_duration_error(request: ProcessingRequest, error: Exception) -> bool:
    """Recognize the provider's per-request duration rejection for diarization.

    Small, low-bitrate recordings can remain below the upload-size limit while
    exceeding the diarization model's duration limit. Retrying only this known
    validation failure avoids requiring ffprobe for every normal single upload.
    """
    if not (request.speakers and request.model == DEFAULT_DIARIZATION_MODEL):
        return False
    message = str(error).casefold()
    return "audio duration" in message and "maximum for this model" in message


def _merge_text_overlap(previous: str, current: str) -> str:
    """Remove a repeated word suffix/prefix introduced by audio overlap."""
    previous_words = previous.split()
    current_words = current.split()
    maximum = min(len(previous_words), len(current_words))
    for count in range(maximum, 0, -1):
        if [word.casefold() for word in previous_words[-count:]] == [word.casefold() for word in current_words[:count]]:
            current_words = current_words[count:]
            break
    return " ".join((*previous_words, *current_words))


def _combine_chunk_transcripts(chunks: list[tuple[AudioChunk, Transcript]]) -> Transcript:
    """Offset chunk metadata and reconcile only speech proven to overlap."""
    combined_text = ""
    combined_segments: list[TranscriptSegment] = []
    latest_end: float | None = None
    speaker_labels: dict[tuple[int, str], str] = {}

    for chunk_index, (chunk, transcript) in enumerate(chunks):
        combined_text = _merge_text_overlap(combined_text, transcript.text)
        for segment in transcript.segments:
            start = segment.start + chunk.start if segment.start is not None else None
            end = segment.end + chunk.start if segment.end is not None else None
            # Do not discard a segment unless an earlier response demonstrably
            # covers its entire interval; preserving uncertain overlap is safer
            # than omitting boundary speech.
            if end is not None and latest_end is not None and end <= latest_end:
                continue
            speaker = segment.speaker
            if speaker is not None:
                key = (chunk_index, speaker)
                speaker = speaker_labels.setdefault(key, f"Speaker {len(speaker_labels) + 1}")
            combined_segments.append(TranscriptSegment(segment.text, speaker, start, end))
            if end is not None:
                latest_end = max(latest_end or end, end)
    return Transcript(
        text=combined_text,
        segments=tuple(combined_segments),
        chunk_count=len(chunks),
        was_chunked=True,
    )


def transcribe(request: ProcessingRequest, client: OpenAIClient) -> Transcript:
    """Submit audio and return a normalized result using an injectable client."""
    try:
        options = _transcription_options(request)
        size = request.audio_file.stat().st_size
        LOGGER.info(
            "Transcribing %s (%.1f MB) with %s.",
            request.audio_file.name,
            size / (1024 * 1024),
            request.model,
        )
        if size <= MAX_TRANSCRIPTION_UPLOAD_BYTES:
            LOGGER.info("Submitting a single audio upload.")
            try:
                with request.audio_file.open("rb") as audio_file:
                    response = client.audio.transcriptions.create(file=audio_file, **options)
            except Exception as error:
                if not _is_diarization_duration_error(request, error):
                    raise
                LOGGER.info(
                    "Audio exceeds the diarization duration limit; splitting it before retrying."
                )
            else:
                transcript = normalize_transcription(response)
                LOGGER.info(
                    "Transcription complete: %d characters across %d segments.",
                    len(transcript.text),
                    len(transcript.segments),
                )
                return transcript
        else:
            LOGGER.info("Audio exceeds the upload limit; splitting it before transcription.")

        with TemporaryDirectory(prefix="meeting-transcript-") as temporary_directory:
            chunks = create_audio_chunks(request.audio_file, Path(temporary_directory))
            responses: list[tuple[AudioChunk, Transcript]] = []
            for index, chunk in enumerate(chunks, start=1):
                LOGGER.info(
                    "Submitting chunk %d/%d (starting at %.1f seconds).",
                    index,
                    len(chunks),
                    chunk.start,
                )
                with chunk.path.open("rb") as audio_file:
                    response = client.audio.transcriptions.create(file=audio_file, **options)
                responses.append((chunk, normalize_transcription(response)))
        transcript = _combine_chunk_transcripts(responses)
        LOGGER.info(
            "Transcription complete: %d characters across %d segments.",
            len(transcript.text),
            len(transcript.segments),
        )
        return transcript
    except TranscriptionError:
        raise
    except Exception as error:
        LOGGER.exception("Transcription failed; provider details follow.")
        raise TranscriptionError(
            f"Transcription request failed: {error}"
        ) from error


def _response_text(response: object) -> str:
    """Extract text from an SDK response or a dict-shaped test fixture."""
    text = _value(response, "output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    output = _value(response, "output", ()) or ()
    fragments: list[str] = []
    for item in output:
        for content in _value(item, "content", ()) or ():
            if _value(content, "type") == "refusal":
                raise SummaryError("The summary request was refused by the model.")
            value = _value(content, "text")
            if isinstance(value, str):
                fragments.append(value)
    result = "\n".join(fragments).strip()
    if not result:
        raise SummaryError("The summary model returned no usable text.")
    return result


def validate_summary(markdown: str) -> MeetingSummary:
    """Reject malformed model output before it becomes a user artifact."""
    normalized = markdown.strip()
    if not normalized:
        raise SummaryError("The summary model returned an empty summary.")
    lines = normalized.splitlines()
    positions: list[int] = []
    missing: list[str] = []
    for heading in SUMMARY_SECTION_HEADINGS:
        try:
            positions.append(lines.index(heading))
        except ValueError:
            missing.append(heading)
    if missing:
        raise SummaryError(
            "The summary model returned an incomplete summary; missing required sections: "
            + ", ".join(missing)
        )
    if positions != sorted(positions):
        raise SummaryError("The summary model returned required sections in the wrong order.")
    return MeetingSummary(markdown=normalized + "\n")


def _split_summary_input(text: str, limit: int = SUMMARY_CHUNK_CHARS) -> tuple[str, ...]:
    """Split on paragraph boundaries where possible, without losing any text."""
    if len(text) <= limit:
        return (text,)

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        boundary = remaining.rfind("\n\n", 0, limit + 1)
        if boundary <= 0:
            boundary = remaining.rfind(" ", 0, limit + 1)
        if boundary <= 0:
            boundary = limit
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:].lstrip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)


def _request_summary_text(client: OpenAIClient, prompt: str) -> str:
    try:
        response = client.responses.create(
            model=DEFAULT_SUMMARY_MODEL,
            input=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return _response_text(response)
    except SummaryError:
        raise
    except Exception as error:
        LOGGER.exception("Summary generation failed; provider details follow.")
        raise SummaryError(
            f"Summary request failed: {error}"
        ) from error


def summarize(transcript: Transcript, client: OpenAIClient) -> MeetingSummary:
    """Generate a validated summary, synthesizing every chunk when needed."""
    transcript_markdown = render_transcript(transcript)
    LOGGER.info("Generating a summary from %d transcript characters.", len(transcript_markdown))
    if len(transcript_markdown) <= SUMMARY_CONTEXT_BUDGET_CHARS:
        LOGGER.info("Submitting one summary request to %s.", DEFAULT_SUMMARY_MODEL)
        summary = validate_summary(_request_summary_text(
            client, "Summarize this meeting transcript:\n\n" + transcript_markdown,
        ))
        LOGGER.info("Summary generation complete.")
        return summary

    chunk_summaries: list[str] = []
    chunks = _split_summary_input(transcript_markdown, limit=SUMMARY_CHUNK_CHARS)
    LOGGER.info("Splitting the transcript into %d summary requests.", len(chunks))
    for index, chunk in enumerate(chunks, start=1):
        LOGGER.info("Summarizing transcript chunk %d/%d.", index, len(chunks))
        chunk_summaries.append(_request_summary_text(
            client,
            f"Summarize transcript chunk {index}. Preserve every supported decision, "
            "proposal, action, question, risk, disagreement, and uncertainty. Do not "
            "infer facts from other chunks.\n\n" + chunk,
        ))
    synthesis_input = "\n\n".join(
        f"# Chunk summary {index}\n\n{summary}"
        for index, summary in enumerate(chunk_summaries, start=1)
    )
    LOGGER.info("Synthesizing the chunk summaries into the final summary.")
    summary = validate_summary(_request_summary_text(
        client,
        "Synthesize the following complete set of chunk summaries into one meeting "
        "summary. Preserve material content from every chunk and explicitly flag any "
        "uncertainty or conflict.\n\n" + synthesis_input,
    ))
    LOGGER.info("Summary generation complete.")
    return summary


def render_transcript(transcript: Transcript) -> str:
    """Render a simple Markdown artifact while retaining anonymous labels."""
    if not transcript.segments:
        return f"# Transcript\n\n{transcript.text}\n"

    lines = ["# Transcript", ""]
    for segment in transcript.segments:
        timestamp = ""
        if segment.start is not None:
            timestamp = f" [{segment.start:.2f}s]"
        label = segment.speaker or "Transcript"
        lines.extend((f"## {label}{timestamp}", "", segment.text, ""))
    return "\n".join(lines)


def _chunking_details(transcript: Transcript) -> str:
    """Describe the actual upload strategy without exposing temporary paths."""
    if not transcript.was_chunked:
        return "Single upload; no chunking was required."
    return (
        f"{transcript.chunk_count} overlapping upload chunks "
        f"({CHUNK_OVERLAP_SECONDS:g}-second overlap, {CHUNK_AUDIO_BITRATE // 1000} kbps AAC)."
    )


def render_report(
    transcript: Transcript, summary: MeetingSummary, request: ProcessingRequest,
) -> str:
    """Render provenance, summary, and the full source transcript as evidence."""
    created_at = request.created_at.astimezone().isoformat(timespec="seconds")
    metadata = "\n".join((
        "## Artifact details",
        "",
        f"- Source filename: `{request.audio_file.name}`",
        f"- Created: {created_at}",
        f"- Transcription model: `{request.model}`",
        f"- Summary model: `{DEFAULT_SUMMARY_MODEL}`",
        f"- Chunking: {_chunking_details(transcript)}",
    ))
    return (
        f"# Meeting summary\n\n{metadata}\n\n{summary.markdown.rstrip()}\n\n"
        f"---\n\n{render_transcript(transcript)}"
    )


def write_report_atomically(destination: Path, report: str) -> None:
    """Publish a complete report with a single atomic replacement operation."""
    LOGGER.info("Writing the Markdown artifact to %s.", destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(report)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
    except OSError as error:
        LOGGER.exception("Could not write the Markdown artifact.")
        raise WorkflowError(f"Could not write transcription artifact: {destination}. Details: {error}") from error
    finally:
        temporary_path.unlink(missing_ok=True)
    LOGGER.info("Markdown artifact written successfully.")


def run_workflow(request: ProcessingRequest, *, client: OpenAIClient | None = None) -> Transcript:
    """Transcribe *request*, summarize it, and write one evidence-rich report."""
    LOGGER.info("Preparing the OpenAI client.")
    active_client = client or get_openai_client()
    transcript = transcribe(request, active_client)
    summary = summarize(transcript, active_client)
    write_report_atomically(request.output_file, render_report(transcript, summary, request))
    return transcript
