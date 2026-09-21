"""Server-side wrapper for the Context.dev API.

All Context.dev traffic in PAT goes through this module — never call the API
(or the ``context.dev`` SDK) from Dash callbacks or other modules directly.

Conventions (see AGENTS.md):
- API key comes from the ``CONTEXT_DEV_API_KEY`` environment variable only.
  It is never hardcoded and never sent to the browser.
- One explicit user action = one API call. No polling loops, no auto-fetch.
- ``429``: surface ``RateLimited`` with the ``Retry-After`` delay; the caller
  tells the user when to retry.
- ``408``/connection errors/``5xx``: the SDK retries with bounded backoff
  (``max_retries``); if it still fails we raise ``UpstreamError``.
- Validation errors (``400``) and brand-not-found (``404``) are never retried.
- Tests must mock this module; no live calls in automated tests.
"""

import os

from context.dev import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ContextDev,
    NotFoundError,
    RateLimitError,
)

BASE_URL = "https://api.context.dev/v1"
_REQUEST_TAGS = ["pat", "naics"]
_DEFAULT_TIMEOUT_S = 45.0
_DEFAULT_MAX_RETRIES = 2


class ContextDevClientError(Exception):
    """Base class for all wrapper errors."""


class NoApiKey(ContextDevClientError):
    """CONTEXT_DEV_API_KEY is not set in the environment."""


class CompanyNotFound(ContextDevClientError):
    """Context.dev could not resolve the company to a brand (HTTP 404)."""


class InvalidLookup(ContextDevClientError):
    """The lookup request was rejected as invalid (HTTP 400); do not retry."""


class RateLimited(ContextDevClientError):
    """Rate limit hit. Honor ``retry_after`` (seconds) before retrying."""

    def __init__(self, message="Rate limit exceeded.", retry_after=30):
        super().__init__(message)
        self.retry_after = retry_after


class UpstreamError(ContextDevClientError):
    """Timeout, connection failure, or 5xx after bounded retries."""


def _client(timeout_s=_DEFAULT_TIMEOUT_S):
    key = os.environ.get("CONTEXT_DEV_API_KEY")
    if not key:
        raise NoApiKey(
            "CONTEXT_DEV_API_KEY is not set. Add it to the .env file "
            "(see README) and restart the server."
        )
    return ContextDev(
        api_key=key,
        base_url=BASE_URL,
        timeout=timeout_s,
        max_retries=_DEFAULT_MAX_RETRIES,
    )


def _retry_after_seconds(exc):
    try:
        raw = exc.response.headers.get("retry-after")
        return max(1, min(60, int(raw)))
    except Exception:
        return 30


def get_naics(company, min_results=1, max_results=3, timeout_s=_DEFAULT_TIMEOUT_S):
    """Look up 2022 NAICS codes for a company name or domain.

    Returns ``{"codes": [{"code", "name", "confidence"}], "domain",
    "credits_remaining", "request_id"}``. Costs 10 credits per call.
    """
    name = (company or "").strip()
    if len(name) < 4:
        raise InvalidLookup("Enter a company name or domain (at least 4 characters).")
    client = _client(timeout_s)
    try:
        resp = client.industry.retrieve_naics(
            input=name,
            min_results=max(1, min_results),
            max_results=max(1, min(10, max_results)),
            tags=_REQUEST_TAGS,
            timeout=timeout_s,
        )
    except RateLimitError as exc:
        raise RateLimited(retry_after=_retry_after_seconds(exc))
    except NotFoundError:
        raise CompanyNotFound(f"No company match found for {name!r}.")
    except BadRequestError as exc:
        raise InvalidLookup(f"Lookup rejected: {exc}")
    except AuthenticationError as exc:
        raise NoApiKey(f"Context.dev key rejected: {exc}")
    except (APITimeoutError, APIConnectionError) as exc:
        raise UpstreamError(f"Context.dev request failed: {exc}")
    except APIStatusError as exc:
        raise UpstreamError(f"Context.dev error (HTTP {exc.status_code}): {exc}")
    codes = [
        {
            "code": getattr(c, "code", ""),
            "name": getattr(c, "name", ""),
            "confidence": getattr(c, "confidence", ""),
        }
        for c in (getattr(resp, "codes", None) or [])
    ]
    key_meta = getattr(resp, "key_metadata", None) or {}
    remaining = (
        key_meta.get("credits_remaining")
        if isinstance(key_meta, dict)
        else getattr(key_meta, "credits_remaining", None)
    )
    return {
        "codes": codes,
        "domain": getattr(resp, "domain", None),
        "credits_remaining": remaining,
        "request_id": getattr(resp, "request_id", None),
    }
