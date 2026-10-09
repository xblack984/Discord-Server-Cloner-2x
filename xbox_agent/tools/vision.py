"""Pick the vision backend from the settings (imports stay lazy: only the chosen SDK is needed), and make
it resilient: one rate-limit, timeout or malformed answer must not stop a session and bother the admin."""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Sequence

from ..config import Settings
from .protocols import Vision

log = logging.getLogger("xbox_agent.vision")

PROVIDERS = ("gemini", "deepseek", "claude")
SLOW_CALL_S = 8.0
_HTTP_TRANSIENT = {408, 409, 425, 429, 529}
_TRANSIENT_NAMES = ("timeout", "connect", "network", "ratelimit", "overload", "internalserver",
                    "serviceunavailable", "remoteprotocol", "readerror")


def is_transient(error: Exception) -> bool:
    """Would asking again plausibly work? Rate limits, timeouts, 5xx and unusable answers: yes.
    Wrong key (401/403), no balance (402), a refusal, or a bug in our code: no."""
    text = str(error)
    if "declined" in text or "content_filter" in text:
        return False
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        found = re.search(r"HTTP (\d{3})", text)
        status = int(found.group(1)) if found else None
    if status is not None:
        return status in _HTTP_TRANSIENT or 500 <= status < 600
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if any(key in type(error).__name__.lower() for key in _TRANSIENT_NAMES):
        return True
    return isinstance(error, ValueError)  # non-JSON / cut off / empty / missing keys: the next answer is often fine


class RetryingVision:
    """Wraps any Vision backend: retry transient failures with exponential backoff, time every call."""

    def __init__(self, inner: Vision, attempts: int = 3, base_delay_s: float = 1.5,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.inner, self.attempts, self.base_delay_s = inner, max(1, attempts), base_delay_s
        self._sleep, self._clock = sleep, clock
        self.last_latency_s = 0.0

    def ask(self, png: bytes, task: str, required_keys: Sequence[str], schema: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(1, self.attempts + 1):
            started = self._clock()
            try:
                out = self.inner.ask(png, task, required_keys, schema)
            except Exception as e:
                if attempt >= self.attempts or not is_transient(e):
                    raise
                delay = min(10.0, self.base_delay_s * 2 ** (attempt - 1))
                log.warning("vision call failed (%s: %s); retry %d/%d in %.1fs", type(e).__name__,
                            str(e)[:160], attempt, self.attempts - 1, delay)
                self._sleep(delay)
                continue
            self.last_latency_s = self._clock() - started
            (log.warning if self.last_latency_s > SLOW_CALL_S else log.debug)(
                "vision call took %.1fs", self.last_latency_s)
            return out
        raise AssertionError("unreachable")


def build_backend(cfg: Settings) -> Vision:
    provider = cfg.vision_provider.lower()
    if provider == "auto":  # first key found: Gemini, then DeepSeek, otherwise Claude (SDK credentials)
        provider = "gemini" if cfg.gemini_api_key else "deepseek" if cfg.deepseek_api_key else "claude"
    if provider == "gemini":
        from .gemini_vision import GeminiVision

        return GeminiVision(cfg.gemini_api_key, cfg.gemini_model)
    if provider == "deepseek":
        from .deepseek_vision import DeepSeekVision

        return DeepSeekVision(cfg.deepseek_api_key, cfg.deepseek_model, cfg.deepseek_base_url, cfg.deepseek_thinking)
    if provider == "claude":
        from .claude_vision import ClaudeVision

        return ClaudeVision(cfg.claude_model, cfg.claude_effort, cfg.claude_fallbacks)
    raise ValueError(f"unknown vision provider '{cfg.vision_provider}' (expected auto, {', '.join(PROVIDERS)})")


def build_vision(cfg: Settings) -> Vision:
    backend = build_backend(cfg)
    return backend if cfg.vision_attempts <= 1 else RetryingVision(backend, cfg.vision_attempts)
