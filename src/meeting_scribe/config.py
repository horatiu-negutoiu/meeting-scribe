"""OpenAI client configuration.

Credentials stay outside project files and are read only when an API call is
about to be made.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI


class MissingAPIKeyError(RuntimeError):
    """Raised when the OpenAI API key is not configured."""


OPENAI_REQUEST_TIMEOUT_SECONDS = 300.0


def require_api_key(environ: dict[str, str] | None = None) -> str:
    """Return the configured API key or raise an actionable error.

    ``environ`` exists to make configuration behavior easy to test without
    changing the process environment.
    """
    values = os.environ if environ is None else environ
    api_key = values.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "OPENAI_API_KEY is not set. Create a key, export it in your shell, "
            "then run the command again."
        )
    return api_key


def get_openai_client(environ: dict[str, str] | None = None) -> OpenAI:
    """Create an authenticated official OpenAI SDK client.

    Importing lazily preserves clear missing-key errors even in tooling that
    only imports this package without installing its runtime dependencies.
    """
    from openai import OpenAI

    return OpenAI(
        api_key=require_api_key(environ),
        timeout=OPENAI_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )
