import logging
from typing import Optional

from app.actions.models import Action, ActionCreate, ActionType
from app.actions.service import ActionService, get_action_service
from app.agent.llm.adapter import LLMAdapter
from app.agent.models import AskResponse, ToolCall
from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.evidence.models import RefusalCode
from app.tools.models import VerifiedReviewersResponse
from app.tools.registry import (
    InvalidToolArgumentsError,
    ToolRegistry,
    UnknownToolError,
    tool_registry,
)

logger = logging.getLogger(__name__)


class AgentCoordinator:
    """
    Main Anvaya Agent Coordinator.
    Enforces the Trust Invariant:
    1. Orchestrates natural-language question -> tool call via LLM adapter.
    2. Validates tool name and arguments deterministically in ToolRegistry.
    3. Rejects unknown tools or malformed arguments before database access.
    4. Executes verified tool against FalkorDB.
    5. Evaluates refusal conditions (NO_PATH, STALE_DATA, etc.).
    6. Produces verified factual narration without hallucinated scores or adjectives.
    7. Proposes verified request_review action with human approval requirement.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMAdapter,
        db_manager: Optional[FalkorDBManager] = None,
        action_service: Optional[ActionService] = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.db_manager = db_manager or get_falkor_manager()
        self.action_service = action_service or get_action_service()


    def ask(self, message: str, repo: str = "arth") -> AskResponse:
        logger.info("Received ask request: repo='%s', message='%s'", repo, message)

        # 1. Tool Selection
        try:
            tool_call: ToolCall = self.llm_adapter.select_tool(message, repo=repo)
        except Exception as exc:
            logger.error("Tool selection failed: %s", exc)
            tool_call = ToolCall(tool="out_of_scope", arguments={})

        # 2. Scope & Registry Verification
        if not tool_call or tool_call.tool == "out_of_scope" or tool_call.tool not in self.tool_registry.tools:
            refusal_reason = (
                "Query is out of scope. Anvaya only routes verified repository queries to registered safe tools."
            )
            return AskResponse(
                question=message,
                selected_tool=tool_call.tool if tool_call else None,
                tool_arguments=tool_call.arguments if tool_call else {},
                refused=True,
                reason_code=RefusalCode.OUT_OF_SCOPE,
                reason=refusal_reason,
                narration=f"[Refusal: OUT_OF_SCOPE] {refusal_reason}",
            )

        # 3. Tool Execution & Argument Validation
        try:
            tool_result = self.tool_registry.execute_tool(
                tool_call.tool,
                tool_call.arguments,
                db_manager=self.db_manager,
            )
        except InvalidToolArgumentsError as exc:
            refusal_reason = f"Invalid tool arguments: {str(exc)}"
            return AskResponse(
                question=message,
                selected_tool=tool_call.tool,
                tool_arguments=tool_call.arguments,
                refused=True,
                reason_code=RefusalCode.OUT_OF_SCOPE,
                reason=refusal_reason,
                narration=f"[Refusal: OUT_OF_SCOPE] {refusal_reason}",
            )
        except UnknownToolError as exc:
            refusal_reason = f"Unknown tool requested: {str(exc)}"
            return AskResponse(
                question=message,
                selected_tool=tool_call.tool,
                tool_arguments=tool_call.arguments,
                refused=True,
                reason_code=RefusalCode.OUT_OF_SCOPE,
                reason=refusal_reason,
                narration=f"[Refusal: OUT_OF_SCOPE] {refusal_reason}",
            )
        except Exception as exc:
            logger.exception("Unexpected error executing tool %s: %s", tool_call.tool, exc)
            refusal_reason = f"Internal execution failure: {str(exc)}"
            return AskResponse(
                question=message,
                selected_tool=tool_call.tool,
                tool_arguments=tool_call.arguments,
                refused=True,
                reason_code=RefusalCode.NO_PATH,
                reason=refusal_reason,
                narration=f"[Refusal: NO_PATH] {refusal_reason}",
            )

        # 4. Handle Result, Verified Narration, and Action Proposal
        if isinstance(tool_result, VerifiedReviewersResponse):
            narration = self.llm_adapter.narrate(message, tool_result)

            proposed_action: Optional[Action] = None
            if not tool_result.refused and tool_result.candidates:
                top_cand = tool_result.candidates[0]
                cand_ev_ids = [
                    ev.id for ev in tool_result.evidence
                    if ev.properties.get("author") == top_cand.person_name
                ] or [ev.id for ev in tool_result.evidence]

                cand_cl_ids = [
                    c.claim_id for c in tool_result.claims
                    if c.subject == top_cand.person_name
                ] or [c.claim_id for c in tool_result.claims]

                try:
                    action_payload = ActionCreate(
                        type=ActionType.REQUEST_REVIEW,
                        repo=tool_result.repo_name,
                        target=tool_result.target,
                        reviewer=top_cand.person_name,
                        reviewer_email=top_cand.person_email,
                        reason=f"Candidate '{top_cand.person_name}' has {top_cand.commit_count} historical commits touching {tool_result.target_type} '{tool_result.target}'.",
                        evidence_ids=cand_ev_ids,
                        claim_ids=cand_cl_ids,
                        arguments={
                            "reviewer": top_cand.person_name,
                            "reviewer_email": top_cand.person_email,
                            "target": tool_result.target,
                            "repo": tool_result.repo_name,
                            "commit_count": top_cand.commit_count,
                            "lines_added": top_cand.lines_added,
                            "lines_deleted": top_cand.lines_deleted,
                        },
                    )
                    proposed_action = self.action_service.create_action(action_payload)
                except Exception as exc:
                    logger.warning("Failed to propose action: %s", exc)
                    proposed_action = None

            return AskResponse(
                question=message,
                selected_tool=tool_call.tool,
                tool_arguments=tool_call.arguments,
                refused=tool_result.refused,
                verified=not tool_result.refused,
                reason_code=tool_result.reason_code,
                reason=tool_result.reason,
                claims=tool_result.claims,
                evidence=tool_result.evidence,
                candidates=tool_result.candidates,
                excluded_candidates=tool_result.excluded_candidates,
                narration=narration,
                sync_info=tool_result.sync_info,
                is_stale=(tool_result.reason_code == RefusalCode.STALE_DATA),
                proposed_action=proposed_action,
            )

        # Fallback for unexpected tool output type
        return AskResponse(
            question=message,
            selected_tool=tool_call.tool,
            tool_arguments=tool_call.arguments,
            refused=True,
            verified=False,
            reason_code=RefusalCode.OUT_OF_SCOPE,
            reason="Tool output could not be mapped to verified response.",
            narration="[Refusal: OUT_OF_SCOPE] Tool output could not be mapped to verified response.",
        )


_agent_coordinator: Optional[AgentCoordinator] = None


def get_agent_coordinator() -> AgentCoordinator:
    """Returns the singleton AgentCoordinator instance."""
    global _agent_coordinator
    if _agent_coordinator is None:
        _agent_coordinator = AgentCoordinator(
            tool_registry=tool_registry,
            llm_adapter=LLMAdapter(),
            db_manager=get_falkor_manager(),
            action_service=get_action_service(),
        )
    return _agent_coordinator
