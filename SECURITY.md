# Security policy

## Supported versions

Security fixes are applied to the latest released version of Meeting Scribe.
If you are running from a source checkout, update to the latest commit before
reporting whether an issue is reproducible.

## Reporting a vulnerability

Please report suspected vulnerabilities privately to
[pypi@horatiu.ca](mailto:pypi@horatiu.ca). Do not open a public issue for a
vulnerability before a fix is available.

Include, where possible:

- a concise description of the impact and affected version;
- steps to reproduce or a minimal proof of concept;
- any relevant logs with API keys, recordings, transcripts, and personal data
  removed; and
- a way to contact you for follow-up.

You should receive an acknowledgement within seven days. Reports will be
assessed, and a remediation plan or request for more information will follow as
soon as practical.

## Handling API keys and meeting data

`OPENAI_API_KEY` is the only credential Meeting Scribe reads. Keep it out of
source control, shell history, command-line arguments, recordings, transcripts,
and shared logs. Prefer a secret manager or other protected environment-based
configuration.

Meeting recordings and transcripts can contain confidential or personal data.
The tool uploads the recording and transcript to the OpenAI API during
processing, then writes the resulting report locally. It does not automatically
redact or delete those files. Process only material you are authorized to
share, and follow your organization's consent, retention, and data-handling
requirements.
