"""Pick the vision backend from the settings (imports stay lazy: only the chosen SDK is needed)."""
from __future__ import annotations

from ..config import Settings
from .protocols import Vision

PROVIDERS = ("gemini", "deepseek", "claude")


def build_vision(cfg: Settings) -> Vision:
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
