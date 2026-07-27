"""Command-line interface for meeting transcript processing."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from meeting_scribe.config import MissingAPIKeyError, require_api_key
from meeting_scribe.workflow import ProcessingRequest, WorkflowError, run_workflow

SUPPORTED_AUDIO_EXTENSIONS = frozenset({".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".wav", ".webm"})
DEFAULT_LANGUAGE = "en"
DEFAULT_MODEL = "gpt-4o-transcribe-diarize"
LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level command parser."""
    parser = argparse.ArgumentParser(
        prog="meeting-scribe",
        description="Create a transcript and summary from a meeting recording.",
    )
    parser.add_argument("audio_file", type=Path, help="path to a local audio recording")
    parser.add_argument(
        "--output-dir",
        type=Path,
        metavar="DIR",
        help="directory for generated output (defaults to the input file's directory)",
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        metavar="CODE",
        help=f"spoken-language code (default: {DEFAULT_LANGUAGE})",
    )
    parser.add_argument(
        "--no-speakers",
        action="store_true",
        help="do not request speaker labels",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        metavar="MODEL",
        help=f"transcription model override (default: {DEFAULT_MODEL})",
    )
    return parser


def _validate_audio_file(path: Path, parser: argparse.ArgumentParser) -> Path:
    """Return a resolved readable audio path or end with a CLI error."""
    if not path.exists():
        parser.error(f"audio file does not exist: {path}")
    if not path.is_file():
        parser.error(f"audio path must be a regular file: {path}")
    try:
        with path.open("rb"):
            pass
    except OSError:
        parser.error(f"audio file is not readable: {path}")
    if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        formats = ", ".join(sorted(SUPPORTED_AUDIO_EXTENSIONS))
        parser.error(f"unsupported audio format '{path.suffix or '(none)'}'; supported formats: {formats}")
    return path.resolve()


def _local_time() -> datetime:
    """Return the local, timezone-aware time used throughout one invocation."""
    return datetime.now().astimezone()


def _output_path(
    audio_file: Path, output_dir: Path | None, created_at: datetime,
) -> Path:
    """Choose a timestamped artifact path, avoiding same-second collisions."""
    directory = audio_file.parent if output_dir is None else output_dir.expanduser().resolve()
    stem = f"transcription-output-{created_at.strftime('%Y%m%d-%H%M%S')}"
    candidate = directory / f"{stem}.md"
    suffix = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{suffix}.md"
        suffix += 1
    return candidate


def _validate_output_path(path: Path, parser: argparse.ArgumentParser) -> None:
    """Reject impossible destinations before the API is called."""
    if path.parent.exists() and not path.parent.is_dir():
        parser.error(f"output directory is not a directory: {path.parent}")


def main(
    argv: Sequence[str] | None = None,
    *,
    workflow_runner: Callable[[ProcessingRequest], None] = run_workflow,
) -> int:
    """Validate CLI input, then hand a complete request to the workflow layer."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    audio_file = _validate_audio_file(args.audio_file.expanduser(), parser)
    created_at = _local_time()
    output_file = _output_path(audio_file, args.output_dir, created_at)
    _validate_output_path(output_file, parser)
    LOGGER.info("Starting meeting transcript for %s.", audio_file)
    LOGGER.info("The report will be written to %s.", output_file)

    try:
        # Read credentials only after local validation has succeeded. This keeps
        # invalid paths and formats from ever initiating API-related work.
        require_api_key()
        LOGGER.info("Credentials validated; beginning processing.")
        workflow_runner(
            ProcessingRequest(
                audio_file=audio_file,
                output_file=output_file,
                language=args.language,
                speakers=not args.no_speakers,
                model=args.model,
                created_at=created_at,
            )
        )
    except MissingAPIKeyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except WorkflowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # API clients expose several exception types.
        print(f"error: API request failed: {error}", file=sys.stderr)
        return 1
    print(f"Created meeting transcript: {output_file}")
    return 0
