from abc import ABC, abstractmethod
from datetime import datetime, timezone
import logging
from typing import Any, Dict

from app.actions.models import Action, ActionType

logger = logging.getLogger(__name__)


class ActionExecutor(ABC):
    """Abstract interface for action execution engines."""

    @abstractmethod
    def execute(self, action: Action) -> Dict[str, Any]:
        """Executes an approved action and returns the execution result."""
        pass


class SandboxActionExecutor(ActionExecutor):
    """
    Deterministic sandbox action executor.
    Simulates external mutations (e.g., GitHub review requests) without requiring external tokens.
    Records clean execution telemetry for FalkorDB provenance.
    """

    def execute(self, action: Action) -> Dict[str, Any]:
        if action.type != ActionType.REQUEST_REVIEW:
            raise ValueError(f"Unsupported action type '{action.type}'. Sandbox only handles '{ActionType.REQUEST_REVIEW.value}'.")

        reviewer = action.arguments.get("reviewer") or action.arguments.get("reviewer_name") or "unknown"
        reviewer_email = action.arguments.get("reviewer_email")

        logger.info(
            "Executing sandbox review request for reviewer='%s' on target='%s' in repo='%s'",
            reviewer,
            action.target,
            action.repo,
        )

        return {
            "success": True,
            "action_type": action.type.value if hasattr(action.type, "value") else str(action.type),
            "target": action.target,
            "reviewer": reviewer,
            "reviewer_email": reviewer_email,
            "sandbox": True,
            "message": f"Review request recorded in sandbox for reviewer '{reviewer}' on target '{action.target}'.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
