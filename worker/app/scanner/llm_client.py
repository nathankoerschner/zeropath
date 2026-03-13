"""OpenAI LLM client with structured JSON parsing and retry/repair logic."""

import json
import logging
from typing import Any

from openai import OpenAI

from app.config import settings
from app.scanner.prompts import REPAIR_SYSTEM_SUFFIX

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Lazy-initialise the OpenAI client."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


class LLMParseError(Exception):
    """Raised when the model output cannot be parsed into valid JSON."""


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from model output.

    Handles common issues like markdown fences around JSON.
    """
    cleaned = text.strip()

    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        # Remove opening fence (possibly ```json)
        first_newline = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()

    return json.loads(cleaned)


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """Call the OpenAI chat API and return parsed JSON.

    If the response is not valid JSON or does not parse, retry with a repair
    prompt up to *max_retries* times (default from settings).

    Raises LLMParseError if all attempts fail.
    """
    if max_retries is None:
        max_retries = settings.max_file_retries

    client = _get_client()
    model = settings.openai_model
    last_error: str = ""
    last_raw: str = ""

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(1 + max_retries):
        try:
            if attempt > 0:
                # Append repair instruction
                repair_msg = REPAIR_SYSTEM_SUFFIX.format(parse_error=last_error)
                call_messages = messages + [
                    {"role": "assistant", "content": last_raw},  # type: ignore[possibly-undefined]
                    {"role": "user", "content": repair_msg},
                ]
            else:
                call_messages = messages

            response = client.chat.completions.create(
                model=model,
                messages=call_messages,  # type: ignore[arg-type]
                temperature=0.2,
                max_tokens=4096,
            )

            raw = response.choices[0].message.content or ""
            last_raw = raw

            parsed = _extract_json(raw)
            return parsed

        except (json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "LLM parse attempt %d/%d failed: %s",
                attempt + 1,
                1 + max_retries,
                last_error,
            )

    raise LLMParseError(
        f"Failed to parse LLM output after {1 + max_retries} attempts. Last error: {last_error}"
    )
