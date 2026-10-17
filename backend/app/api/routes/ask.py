from fastapi import APIRouter, Depends
from app.agent.coordinator import AgentCoordinator, get_agent_coordinator
from app.agent.models import AskRequest, AskResponse

router = APIRouter(tags=["Agent"])


@router.post("/ask", response_model=AskResponse)
def ask_anvaya(
    req: AskRequest,
    coordinator: AgentCoordinator = Depends(get_agent_coordinator),
) -> AskResponse:
    """
    Submits a natural-language query to the Anvaya Agent.
    Selects a deterministic registered tool, executes verified graph traversal,
    and returns verified claims, evidence, and narration.
    """
    return coordinator.ask(message=req.message, repo=req.repo)
