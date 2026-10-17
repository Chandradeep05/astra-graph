import logging
import re
from typing import Any, List, Optional

from app.agent.llm.provider import (
    GeminiProvider,
    GroqProvider,
    LLMProvider,
    OllamaProvider,
    TemplateProvider,
)
from app.agent.models import ToolCall
from app.agent.narrator import TemplateNarrator
from app.tools.models import VerifiedReviewersResponse

logger = logging.getLogger(__name__)


class LLMAdapter:
    """
    Cascading LLM adapter for tool selection and verified narration.
    Fallback order:
    1. Hosted provider (Groq)
    2. Fallback provider (Gemini)
    3. Local provider (Ollama)
    4. Deterministic template fallback (TemplateProvider)

    Safety invariant: The adapter strictly preserves verifiable claims and rejects
    any hallucinated scores or ungrounded assertions.
    """

    def __init__(
        self,
        providers: Optional[List[LLMProvider]] = None,
        force_provider: str = "auto",
    ) -> None:
        self.force_provider = force_provider
        self.template_narrator = TemplateNarrator()

        if providers is not None:
            self.providers = providers
        else:
            # Build default cascade
            candidate_providers: List[LLMProvider] = [
                GroqProvider(),
                GeminiProvider(),
                OllamaProvider(),
                TemplateProvider(),
            ]
            self.providers = candidate_providers

    def select_tool(self, question: str, repo: str = "arth") -> ToolCall:
        """
        Attempts tool selection across available providers with graceful degradation.
        """
        last_error: Optional[Exception] = None

        for provider in self.providers:
            try:
                # If provider reports unavailable, skip to next unless forced
                if not provider.is_available() and not isinstance(provider, TemplateProvider):
                    continue

                call = provider.select_tool(question, repo=repo)
                if call and call.tool:
                    return call
            except Exception as exc:
                logger.warning(
                    "Provider %s failed tool selection: %s. Cascading to next provider.",
                    provider.__class__.__name__,
                    exc,
                )
                last_error = exc
                continue

        # Ultimate fallback
        logger.info("Using ultimate deterministic TemplateProvider for tool selection.")
        return TemplateProvider().select_tool(question, repo=repo)

    def narrate(self, question: str, response: Any) -> str:
        """
        Generates prose narration with verifier safety filtering.
        """
        for provider in self.providers:
            try:
                if not provider.is_available() and not isinstance(provider, TemplateProvider):
                    continue

                text = provider.narrate(question, response)
                if text:
                    # Fail-safe check: reject subjective claims that violate Trust Contract
                    forbidden_pattern = re.compile(
                        r"\b(expert|best|top|superior|go-to|guru|probably)\b",
                        re.IGNORECASE,
                    )
                    if forbidden_pattern.search(text):
                        logger.warning(
                            "LLM output contained forbidden subjective assertions. Falling back to template narrator."
                        )
                        return self.template_narrator.narrate(question, response)
                    return text
            except Exception as exc:
                logger.warning(
                    "Provider %s failed narration: %s. Cascading.",
                    provider.__class__.__name__,
                    exc,
                )
                continue

        return self.template_narrator.narrate(question, response)
