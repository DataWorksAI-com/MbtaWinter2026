"""
Provider-agnostic LLM client.
Supports Anthropic and OpenAI — auto-detects based on available API keys.
Override with LLM_PROVIDER env var: "anthropic" or "openai"
"""
import os
import asyncio
from typing import Optional

from anthropic import Omit


class LLMClient:
    def __init__(self):
        self.provider = self._detect_provider()
        self.client = self._init_client()

    def _detect_provider(self) -> str:
        # Allow explicit override
        forced = os.getenv("LLM_PROVIDER", "").lower()
        if forced in ("anthropic", "openai"):
            return forced

        # Auto-detect based on available keys
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"

        raise RuntimeError("No LLM API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")

    def _init_client(self):
        if self.provider == "anthropic":
            import anthropic
            return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        else:
            from openai import OpenAI
            return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def complete(
        self, 
        system: str, 
        user: str, 
        max_tokens: int = 500, 
        temperature: float = 0.7,
        response_schema: dict | None = None
    ) -> str:
        """Single unified interface for both providers."""
        if self.provider == "anthropic":
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": response_schema}} if response_schema else Omit()
            )
            return response.content[0].text.strip()
        else:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"} if response_schema else None,
            )
            return response.choices[0].message.content.strip()


# Singleton
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
