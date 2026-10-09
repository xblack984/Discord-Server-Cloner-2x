"""Pick the vision backend from the settings (imports stay lazy: only the chosen SDK is needed)."""
from __future__ import annotations

from ..config import Settings
from .protocols import Vision

PROVIDERS = ("gemini", "claude")


def build_vision(cfg: Settings) -> Vision:
    provider = cfg.vision_provider.lower()
    if provider == "auto":  # a Gemini key keeps the original behaviour; otherwise Claude
        provider = "gemini" if cfg.gemini_api_key else "claude"
    if provider == "gemini":
        from .gemini_vision import GeminiVision

        return GeminiVision(cfg.gemini_api_key, cfg.gemini_model)
    if provider == "claude":
        from .claude_vision import ClaudeVision

        return ClaudeVision(cfg.claude_model, cfg.claude_effort, cfg.claude_fallbacks)
    raise ValueError(f"unknown vision provider '{cfg.vision_provider}' (expected auto, {', '.join(PROVIDERS)})")
