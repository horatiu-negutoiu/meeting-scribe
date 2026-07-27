# Meeting Scribe

Turn a local meeting recording into a speaker-labelled transcript and an
evidence-grounded Markdown summary.

Meeting Scribe is a command-line tool. It reads a recording from your computer,
sends it to the OpenAI API for transcription and summarization, then writes one
Markdown report to your computer. It is designed to make review easy: the
report includes both the summary and the complete transcript it was based on.

> [!IMPORTANT]
> Review the report before sharing it or acting on it. Transcription, speaker
> labels, and generated summaries can be incomplete or incorrect.

## Requirements

- Python 3.14 or newer
- An [OpenAI API key](https://platform.openai.com/api-keys) with access and
  billing for the required API models
- `ffmpeg` **and** `ffprobe` on your `PATH` when a recording must be split
  before upload (see [Large or long recordings](#large-or-long-recordings))

The command accepts these recording extensions: `.m4a`, `.mp3`, `.mp4`,
`.mpeg`, `.mpga`, `.wav`, and `.webm`.

## Install

Choose one installation method. `uv` is a convenient way to run the project
from a checkout; `pip` and `pipx` are appropriate once the package is
published to PyPI.

### Install with `pip`

```console
python -m pip install meeting-transcribe
```

If your platform separates Python 3 from Python, use the Python 3.14-or-newer
interpreter explicitly, for example `python3.14 -m pip install meeting-transcribe`.

### Install with `pipx`

`pipx` installs the command in an isolated environment:

```console
pipx install meeting-transcribe
```

If the command is not found afterwards, run `pipx ensurepath`, open a new
terminal, and try again.

### Run from a checkout with `uv`

Clone the repository, then let `uv` create the environment and install the
package:

```console
git clone https://github.com/horatiu-negutoiu/meeting-scribe.git
cd meeting-scribe
uv sync
```

Run the command through `uv` from that directory:

```console
uv run meeting-scribe --help
```

## Configure your API key

Create a key in the [OpenAI platform](https://platform.openai.com/api-keys),
then set it only in the environment of the terminal that will run Meeting
Scribe:

```console
export OPENAI_API_KEY="your_api_key_here"
```

On PowerShell:

```powershell
$env:OPENAI_API_KEY = "your_api_key_here"
```

Do not put the key in a command-line argument, recording filename, transcript,
or committed configuration file. Meeting Scribe reads only `OPENAI_API_KEY`;
it does not accept credentials as CLI arguments or write them to its report.

For a persistent setup, use your operating system's secret manager, your shell
profile with appropriate file permissions, or a trusted environment manager.
See [Security](SECURITY.md) for reporting a vulnerability and handling secrets.

## Quick start

First, confirm the available options:

```console
meeting-scribe --help
```

Then process a recording:

```console
meeting-scribe path/to/team-sync.m4a
```

When running from a checkout with `uv`, prefix the same command with `uv run`:

```console
uv run meeting-scribe path/to/team-sync.m4a
```

On success, the command prints the exact report path:

```text
Created meeting transcript: /path/to/transcription-output-YYYYMMDD-HHMMSS.md
```

## Command options

```text
meeting-scribe [-h] [--output-dir DIR] [--language CODE]
               [--no-speakers] [--model MODEL] audio_file
```

- `audio_file` is a readable local recording in one of the supported formats.
- `--output-dir DIR` writes the report to `DIR`; without it, the report is
  placed beside the recording.
- `--language CODE` sets the spoken-language code. The default is `en`.
- `--no-speakers` requests a transcript without speaker labels.
- `--model MODEL` overrides the transcription model. The default is
  `gpt-4o-transcribe-diarize`.

For example, to produce a French report without speaker labels in a separate
folder:

```console
meeting-scribe meeting.mp3 --language fr --no-speakers --output-dir reports
```

## What the report contains

Each report is a new timestamped Markdown file named
`transcription-output-YYYYMMDD-HHMMSS.md`. If a file with that name already
exists, Meeting Scribe adds a numeric suffix instead of overwriting it.

The report includes:

- the source filename, creation time, selected transcription and summary
  models, and whether the recording was split into chunks;
- an evidence-grounded summary with decisions, proposals, action items, open
  questions, risks, discussion notes, and evidence; and
- the full transcript.

When speaker labels are requested and returned, they are converted to anonymous
labels such as `Speaker 1`. These labels distinguish voices only; they do not
identify people and can be inconsistent, especially across chunk boundaries.

## Large or long recordings

The transcription upload limit is 25 MB. The default diarization model also has
a per-request duration limit. If a recording is too large, or the API rejects
it as too long for diarization, Meeting Scribe re-encodes it into overlapping,
upload-safe `.m4a` chunks and retries. The overlap helps avoid losing speech at
the boundary.

That fallback requires both `ffmpeg` and `ffprobe` to be installed and
available on `PATH`. Common installation commands include:

```console
# macOS with Homebrew
brew install ffmpeg

# Debian or Ubuntu
sudo apt install ffmpeg

# Windows with winget
winget install Gyan.FFmpeg
```

After installing, open a new terminal and check both commands:

```console
ffmpeg -version
ffprobe -version
```

## Privacy and data handling

Meeting Scribe is local-first in where it stores files, but processing is not
fully local:

- The selected recording is uploaded to the OpenAI API for transcription.
- The transcript is sent to the OpenAI API to generate the summary.
- The final Markdown report is written locally, in the input directory or the
  directory selected with `--output-dir`.
- The tool does not automatically redact recordings or transcripts, delete the
  source recording, delete the report, or upload the report as a separate
  artifact.

Only process recordings you are authorized to share and retain. Confirm the
applicable consent, confidentiality, retention, and OpenAI account data-use
settings for your organization before processing sensitive meetings.

## Limitations

- Speaker labels are anonymous voice groupings, not verified identities.
- The default language is English; set `--language` to match the recording.
- Audio quality, overlapping speakers, accents, background noise, and mixed
  languages can reduce transcription accuracy.
- The summary uses only the transcript, but generated output may still omit
  context or state something incorrectly. Treat it as a draft for human review.
- Splitting a long recording helps meet request limits but may make speaker
  labels less consistent across chunks.
- An API key, network connection, eligible account, and available models are
  required for processing.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `meeting-scribe: command not found` | Reinstall with the chosen method. For `pipx`, run `pipx ensurepath`, open a new terminal, and retry. For a checkout, use `uv run meeting-scribe ...`. |
| `OPENAI_API_KEY is not set` | Export the key in the terminal that runs the command, then rerun it. Do not pass the key as a command argument. |
| `audio file does not exist` or `is not readable` | Verify the path, permissions, and that the file is a regular local file. Quote paths containing spaces. |
| `unsupported audio format` | Convert the recording to `.m4a`, `.mp3`, `.mp4`, `.mpeg`, `.mpga`, `.wav`, or `.webm`. |
| `ffmpeg (including ffprobe) is unavailable` | Install FFmpeg, ensure both commands are on `PATH`, open a new terminal, and verify with `ffmpeg -version` and `ffprobe -version`. |
| `Could not split the recording with ffmpeg` | Confirm that the recording is readable and contains audio, then try again. The error includes FFmpeg details. |
| `API request failed` or a summary failure | Check the API key, network connection, account billing/access, and model availability. Rerun after resolving the provider error. Individual requests time out after five minutes rather than hanging indefinitely. |
| No report was created | Resolve the terminal error and rerun. A report is published only after the workflow completes successfully. |

## Development

```console
uv sync --extra dev
uv run pytest
uv run meeting-scribe --help
```

### Validate a release artifact

Build release artifacts with `uv build`. Before publishing, validate their
metadata and README rendering, then test each artifact in a fresh virtual
environment:

```console
rm -rf dist
uv build
uv run --with twine twine check dist/*

uv venv /tmp/meeting-transcribe-wheel-check
uv pip install --python /tmp/meeting-transcribe-wheel-check/bin/python dist/*.whl
/tmp/meeting-transcribe-wheel-check/bin/meeting-scribe --help

uv venv /tmp/meeting-transcribe-sdist-check
sdist_dir=$(mktemp -d)
tar -xzf dist/*.tar.gz -C "$sdist_dir"
cd "$sdist_dir"/meeting_transcribe-*
uv pip install --python /tmp/meeting-transcribe-sdist-check/bin/python '.[dev]'
/tmp/meeting-transcribe-sdist-check/bin/python -m pytest
/tmp/meeting-transcribe-sdist-check/bin/meeting-scribe --help
```

### Publish a release

Continuous integration runs the test suite, builds both distribution artifacts,
and validates their package metadata on every push and pull request. The
tag-triggered release workflow publishes first to TestPyPI, performs a clean
installation smoke test, then waits for approval before the PyPI upload. Follow
the [release runbook](docs/release-runbook.md) for the one-time Trusted
Publishing setup, release procedure, verification, and remediation steps.

The source distribution is intentionally limited to the source, tests, and
package-release files; inspect its file list before publishing if the build
configuration changes.

The test suite makes no OpenAI requests. It includes a no-network end-to-end
test covering command validation, transcription normalization, summary
generation, and atomic Markdown artifact creation with a fake SDK client.

## Releases and versioning

The first public release is `0.1.0`. Meeting Scribe follows [Semantic
Versioning 2.0.0](https://semver.org/spec/v2.0.0.html): releases use
`MAJOR.MINOR.PATCH` version numbers. Before `1.0.0`, minor releases may include
breaking changes; patch releases contain compatible bug fixes only. Starting
with `1.0.0`, breaking changes require a major-version increase, compatible
features require a minor-version increase, and compatible bug fixes require a
patch-version increase.
