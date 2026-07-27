from __future__ import annotations

import pytest

from meeting_scribe.config import MissingAPIKeyError, OPENAI_REQUEST_TIMEOUT_SECONDS, require_api_key


def test_require_api_key_returns_configured_value() -> None:
    assert require_api_key({"OPENAI_API_KEY": " test-key "}) == "test-key"


@pytest.mark.parametrize("environ", [{}, {"OPENAI_API_KEY": "   "}])
def test_require_api_key_rejects_missing_or_blank_value(environ: dict[str, str]) -> None:
    with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY is not set"):
        require_api_key(environ)


def test_openai_request_timeout_is_bounded_for_a_personal_cli() -> None:
    assert OPENAI_REQUEST_TIMEOUT_SECONDS == 300.0
