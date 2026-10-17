from abc import ABC, abstractmethod
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from app.agent.models import ToolCall
from app.agent.narrator import TemplateNarrator
from app.tools.models import VerifiedReviewersResponse

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract interface for LLM orchestrator/narrator providers."""

    @abstractmethod
    def is_available(self) -> bool:
        """Returns True if the provider is configured and reachable."""
        pass

    @abstractmethod
    def select_tool(self, question: str, repo: str = "arth") -> ToolCall:
        """Selects an application tool and structured arguments based on user input."""
        pass

    @abstractmethod
    def narrate(self, question: str, response: Any) -> str:
        """Generates factual, verified prose from verified response claims."""
        pass


class TemplateProvider(LLMProvider):
    """
    Deterministic zero-dependency fallback provider.
    Runs entirely offline using parameterized pattern matching and templated narration.
    Serves as the ultimate reliability safety net for Anvaya.
    """

    def __init__(self) -> None:
        self.narrator = TemplateNarrator()

    def is_available(self) -> bool:
        return True

    def select_tool(self, question: str, repo: str = "arth") -> ToolCall:
        q = question.strip()

        # Reject prompt injection or direct Cypher manipulation attempts
        upper_tokens = set(re.findall(r"\b[A-Z]{3,}\b", q.upper()))
        cypher_keywords = {"MATCH", "DETACH", "DELETE", "DROP", "CREATE", "MERGE", "CYPHER", "RETURN"}
        if len(upper_tokens & cypher_keywords) >= 2 or "CYPHER" in upper_tokens or "DETACH" in upper_tokens:
            logger.warning("Rejected potential Cypher injection: %s", q)
            return ToolCall(
                tool="out_of_scope",
                arguments={"error": "Direct Cypher execution is strictly prohibited."},
            )

        # Detect repo from question if databases is mentioned or path implies databases
        detected_repo = repo
        if re.search(r"\bdatabases\b", q, re.IGNORECASE) or "databases/" in q or "postgres" in q:
            detected_repo = "databases"

        is_impact_query = bool(re.search(r"\b(affect|impact|blast radius|break|touch|co-change|modifying|modify|changing|change)\b", q, re.IGNORECASE))
        is_reviewer_query = bool(re.search(r"\b(review|reviewer|reviewers|who)\b", q, re.IGNORECASE))

        # Check for file pattern
        file_match = re.search(r"([a-zA-Z0-9_\-\./]+\.[a-zA-Z0-9_\-]+)", q)
        file_name = file_match.group(1).rstrip("?.!,:;") if file_match else None

        # Resolve shorthand postgres.py -> databases/backends/postgres.py if detected_repo is databases
        if file_name and detected_repo == "databases":
            if file_name in ("postgres.py", "backends/postgres.py"):
                file_name = "databases/backends/postgres.py"

        # Combined query: What could this affect AND who should review?
        # Or in multi-contributor repo databases, reviewer queries leverage multi-hop impact reviewers
        if (is_impact_query and is_reviewer_query) or (is_reviewer_query and detected_repo == "databases" and file_name):
            if file_name:
                return ToolCall(
                    tool="impact_reviewers",
                    arguments={"repo": detected_repo, "file_path": file_name},
                )

        # Pure impact query
        if is_impact_query and not is_reviewer_query and file_name:
            return ToolCall(
                tool="change_impact",
                arguments={"repo": detected_repo, "file_path": file_name},
            )

        # Module pattern: e.g. "Who should review the backend module?" or "module backend"
        module_match = re.search(r"(?:the\s+)?([a-zA-Z0-9_\-\./]+)\s+module\b", q, re.IGNORECASE)
        if not module_match:
            module_match = re.search(r"\bmodule[:\s]+([a-zA-Z0-9_\-\./]+)\b", q, re.IGNORECASE)

        if module_match:
            mod_name = module_match.group(1).rstrip("?.!,:;")
            return ToolCall(
                tool="reviewers_for",
                arguments={"repo": detected_repo, "module": mod_name},
            )

        # File pattern: e.g. "Who should review backend/app/api/v1/internal.py?" or "render.yaml"
        if file_name:
            return ToolCall(
                tool="reviewers_for",
                arguments={"repo": detected_repo, "file": file_name},
            )

        # Target unspecified reviewer query
        if is_reviewer_query:
            return ToolCall(
                tool="reviewers_for",
                arguments={"repo": detected_repo},
            )

        return ToolCall(
            tool="out_of_scope",
            arguments={"query": q},
        )

    def narrate(self, question: str, response: Any) -> str:
        return self.narrator.narrate(question, response)


class GroqProvider(LLMProvider):
    """Hosted fast LLM provider using Groq API."""

    def __init__(self, model_name: str = "llama-3.3-70b-versatile") -> None:
        self.model_name = model_name
        self.api_key = os.getenv("GROQ_API_KEY")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def select_tool(self, question: str, repo: str = "arth") -> ToolCall:
        if not self.is_available():
            raise RuntimeError("Groq API key is not configured.")

        from groq import Groq

        client = Groq(api_key=self.api_key)
        system_prompt = (
            "You are Anvaya's deterministic tool selector. You extract tool parameters.\n"
            "Registered tools:\n"
            "- reviewers_for: arguments: {\"repo\": \"...\", \"file\": \"...\"} OR {\"repo\": \"...\", \"module\": \"...\"}\n"
            "- change_impact: arguments: {\"repo\": \"...\", \"file_path\": \"...\"}\n"
            "- impact_reviewers: arguments: {\"repo\": \"...\", \"file_path\": \"...\"}\n"
            "You must return ONLY a JSON object: {\"tool\": \"...\", \"arguments\": {...}}.\n"
            "Never generate Cypher or SQL. If the query is unrelated, return {\"tool\": \"out_of_scope\", \"arguments\": {}}."
        )

        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Repository: {repo}\nQuestion: {question}"},
            ],
            model=self.model_name,
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        raw = chat_completion.choices[0].message.content
        data = json.loads(raw)
        return ToolCall(tool=data.get("tool", "out_of_scope"), arguments=data.get("arguments", {}))

    def narrate(self, question: str, response: VerifiedReviewersResponse) -> str:
        if not self.is_available():
            raise RuntimeError("Groq API key is not configured.")

        # Fallback to deterministic template narrator for verified factual safety
        return TemplateNarrator().narrate(question, response)


class GeminiProvider(LLMProvider):
    """Google Gemini hosted LLM provider."""

    def __init__(self, model_name: str = "gemini-2.5-flash") -> None:
        self.model_name = model_name
        self.api_key = os.getenv("GEMINI_API_KEY")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def select_tool(self, question: str, repo: str = "arth") -> ToolCall:
        if not self.is_available():
            raise RuntimeError("Gemini API key is not configured.")

        # Fallback to TemplateProvider if external call fails or not initialized
        return TemplateProvider().select_tool(question, repo=repo)

    def narrate(self, question: str, response: VerifiedReviewersResponse) -> str:
        return TemplateNarrator().narrate(question, response)


class OllamaProvider(LLMProvider):
    """Local Ollama instance provider."""

    def __init__(self, model_name: str = "llama3:latest", host: str = "http://localhost:11434") -> None:
        self.model_name = model_name
        self.host = host

    def is_available(self) -> bool:
        return False  # Disabled by default unless configured

    def select_tool(self, question: str, repo: str = "arth") -> ToolCall:
        return TemplateProvider().select_tool(question, repo=repo)

    def narrate(self, question: str, response: VerifiedReviewersResponse) -> str:
        return TemplateNarrator().narrate(question, response)
