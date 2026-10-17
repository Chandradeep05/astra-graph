from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.actions.models import (
    Action,
    ActionCreate,
    ActionNotFoundError,
    ActionStatus,
    InvalidActionError,
    InvalidStateTransitionError,
)
from app.actions.service import ActionService, get_action_service

router = APIRouter(prefix="/actions", tags=["Actions"])


class RejectRequest(BaseModel):
    reason: Optional[str] = None


@router.post("", response_model=Action, status_code=status.HTTP_201_CREATED)
def propose_action(
    payload: ActionCreate,
    service: ActionService = Depends(get_action_service),
) -> Action:
    """Proposes a new action with evidence grounding."""
    try:
        return service.create_action(payload)
    except InvalidActionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("", response_model=List[Action])
def list_actions(
    repo: Optional[str] = Query(None, description="Filter by repository"),
    action_status: Optional[ActionStatus] = Query(None, alias="status", description="Filter by status"),
    service: ActionService = Depends(get_action_service),
) -> List[Action]:
    """Lists actions matching optional filters."""
    return service.list_actions(repo=repo, status=action_status)


@router.get("/{action_id}", response_model=Action)
def get_action(
    action_id: str,
    service: ActionService = Depends(get_action_service),
) -> Action:
    """Retrieves an action by ID."""
    try:
        return service.get_action(action_id)
    except ActionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.post("/{action_id}/approve", response_model=Action)
def approve_action(
    action_id: str,
    service: ActionService = Depends(get_action_service),
) -> Action:
    """
    Approves a proposed action (human-in-the-loop gate).
    Transitions PROPOSED -> APPROVED.
    """
    try:
        return service.approve_action(action_id)
    except ActionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/{action_id}/reject", response_model=Action)
def reject_action(
    action_id: str,
    payload: Optional[RejectRequest] = None,
    service: ActionService = Depends(get_action_service),
) -> Action:
    """
    Rejects a proposed action.
    Transitions PROPOSED -> REJECTED.
    """
    try:
        reason = payload.reason if payload else None
        return service.reject_action(action_id, reason=reason)
    except ActionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/{action_id}/execute", response_model=Action)
def execute_action(
    action_id: str,
    service: ActionService = Depends(get_action_service),
) -> Action:
    """
    Executes an approved action via the sandbox executor and writes outcome to FalkorDB.
    Strictly fails if the action has not received human approval.
    """
    try:
        return service.execute_action(action_id)
    except ActionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
