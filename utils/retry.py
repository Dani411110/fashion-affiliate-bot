"""Retry decorators wrapping tenacity for network and API errors."""

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_exception,
    before_sleep_log,
    RetryCallState,
)
import logging

from utils.logger import get_logger

logger = get_logger(__name__)

_stdlib_logger = logging.getLogger("tenacity.retry")


def _is_rate_limit_or_unavailable(exc: BaseException) -> bool:
    """True when the exception represents an HTTP 429 or 503."""
    if isinstance(exc, requests.HTTPError):
        code = getattr(exc.response, "status_code", None)
        return code in (429, 503)
    class_name = type(exc).__name__
    if "RateLimitError" in class_name or "ServiceUnavailableError" in class_name:
        return True
    return False


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Retry attempt {attempt} for {fn} — error: {err}",
        attempt=retry_state.attempt_number,
        fn=retry_state.fn.__qualname__ if retry_state.fn else "unknown",
        err=repr(exc),
    )


retry_on_network_error = retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(
        (
            requests.ConnectionError,
            requests.Timeout,
            requests.HTTPError,
            ConnectionError,
            TimeoutError,
            OSError,
        )
    ),
    before_sleep=_log_retry,
)

retry_on_api_error = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    retry=retry_if_exception(_is_rate_limit_or_unavailable),
    before_sleep=_log_retry,
)
