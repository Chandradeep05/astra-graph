from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from app.actions.executor import ActionExecutor, SandboxActionExecutor
from app.actions.models import (
    Action,
    ActionCreate,
    ActionNotFoundError,
    ActionStatus,
    ActionType,
    InvalidActionError,
    InvalidStateTransitionError,
    Outcome,
)
from app.db.falkor import FalkorDBManager, get_falkor_manager

logger = logging.getLogger(__name__)


class ActionService:
    """
    Deterministic Action Service managing the human-in-the-loop lifecycle
    and FalkorDB graph provenance.

    State Machine:
      PROPOSED -> APPROVED
      PROPOSED -> REJECTED
      APPROVED -> EXECUTING
      EXECUTING -> SUCCEEDED
      EXECUTING -> FAILED
    """

    def __init__(
        self,
        db_manager: Optional[FalkorDBManager] = None,
        executor: Optional[ActionExecutor] = None,
        memory_service: Optional[Any] = None,
        graph_name: Optional[str] = None,
    ) -> None:
        self.db_manager = db_manager or get_falkor_manager()
        self.executor = executor or SandboxActionExecutor()
        self.memory_service = memory_service
        self.graph_name = graph_name
        self._actions: Dict[str, Action] = {}

    def create_action(self, payload: ActionCreate) -> Action:
        """
        Creates a new proposed action.
        Validates evidence grounding and writes Action node and provenance into FalkorDB.
        """
        if not payload.evidence_ids:
            raise InvalidActionError("Action cannot be created without at least one verified evidence ID.")

        if payload.type != ActionType.REQUEST_REVIEW:
            raise InvalidActionError(f"Unsupported action type '{payload.type}'. Only '{ActionType.REQUEST_REVIEW.value}' is allowed.")

        action_id = f"act_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Combine arguments
        args = dict(payload.arguments)
        args.setdefault("reviewer", payload.reviewer)
        if payload.reviewer_email:
            args.setdefault("reviewer_email", payload.reviewer_email)
        args.setdefault("target", payload.target)
        args.setdefault("repo", payload.repo)

        action = Action(
            id=action_id,
            type=payload.type,
            repo=payload.repo,
            target=payload.target,
            reviewer=payload.reviewer,
            arguments=args,
            reason=payload.reason,
            evidence_ids=list(payload.evidence_ids),
            claim_ids=list(payload.claim_ids),
            status=ActionStatus.PROPOSED,
            created_at=now_iso,
        )

        self._actions[action_id] = action

        # Write to FalkorDB
        self._write_action_to_graph(action)

        logger.info("Proposed action created: id=%s, type=%s, reviewer=%s", action.id, action.type, payload.reviewer)
        return action

    def get_action(self, action_id: str) -> Action:
        """Retrieves an action by ID from memory or graph."""
        if action_id in self._actions:
            return self._actions[action_id]

        action = self._read_action_from_graph(action_id)
        if action:
            self._actions[action_id] = action
            return action

        raise ActionNotFoundError(f"Action '{action_id}' does not exist.")

    def list_actions(self, repo: Optional[str] = None, status: Optional[ActionStatus] = None) -> List[Action]:
        """Lists all actions matching optional filters."""
        actions = list(self._actions.values())
        if repo:
            actions = [a for a in actions if a.repo == repo]
        if status:
            actions = [a for a in actions if a.status == status]
        return actions

    def approve_action(self, action_id: str) -> Action:
        """
        Transitions PROPOSED -> APPROVED.
        Enforces human approval invariant.
        """
        action = self.get_action(action_id)

        if action.status != ActionStatus.PROPOSED:
            raise InvalidStateTransitionError(
                f"Cannot approve action '{action_id}' in status '{action.status.value}'. "
                f"Only actions in '{ActionStatus.PROPOSED.value}' status can be approved."
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        action.status = ActionStatus.APPROVED
        action.approved_at = now_iso

        self._actions[action_id] = action
        self._update_action_status_in_graph(action)

        logger.info("Action approved: id=%s", action_id)
        return action

    def reject_action(
        self,
        action_id: str,
        reason: Optional[str] = None,
        constraint: Optional[Any] = None,
    ) -> Action:
        """
        Transitions PROPOSED -> REJECTED.
        Rejected actions can NEVER be executed.
        If a structured constraint is provided, persists it into FalkorDB graph memory
        linked to this action: (:Action)-[:RESULTED_IN]->(:Constraint).
        """
        action = self.get_action(action_id)

        if action.status != ActionStatus.PROPOSED:
            raise InvalidStateTransitionError(
                f"Cannot reject action '{action_id}' in status '{action.status.value}'. "
                f"Only actions in '{ActionStatus.PROPOSED.value}' status can be rejected."
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        action.status = ActionStatus.REJECTED
        action.rejected_at = now_iso
        if reason:
            action.error_message = f"Rejected by user: {reason}"

        # If a structured constraint was submitted, persist to graph memory
        if constraint:
            from app.memory.models import ConstraintCreate, ConstraintType
            from app.memory.service import MemoryService, get_memory_service

            if isinstance(constraint, dict):
                c_payload = ConstraintCreate(
                    type=constraint.get("type", ConstraintType.REVIEWER_UNAVAILABLE),
                    subject=constraint.get("subject") or action.reviewer or action.arguments.get("reviewer", ""),
                    scope=constraint.get("scope") or f"repo:{action.repo}",
                    source_action_id=action.id,
                )
            elif isinstance(constraint, ConstraintCreate):
                c_payload = constraint
                c_payload.source_action_id = action.id
                if not c_payload.subject:
                    c_payload.subject = action.reviewer or action.arguments.get("reviewer", "")
                if not c_payload.scope:
                    c_payload.scope = f"repo:{action.repo}"
            else:
                c_payload = None

            if c_payload:
                mem_svc = self.memory_service or MemoryService(
                    db_manager=self.db_manager,
                    graph_name=self.graph_name,
                )
                mem_svc.create_constraint(c_payload)

        self._actions[action_id] = action
        self._update_action_status_in_graph(action)

        logger.info("Action rejected: id=%s (has_constraint=%s)", action_id, bool(constraint))
        return action

    def execute_action(self, action_id: str) -> Action:
        """
        Transitions APPROVED -> EXECUTING -> SUCCEEDED / FAILED.
        Strict invariant: Execute without approval MUST fail.
        """
        action = self.get_action(action_id)

        if action.status == ActionStatus.PROPOSED:
            raise InvalidStateTransitionError(
                f"Cannot execute action '{action_id}' in status '{ActionStatus.PROPOSED.value}'. "
                "Human approval is mandatory prior to execution."
            )

        if action.status == ActionStatus.REJECTED:
            raise InvalidStateTransitionError(
                f"Cannot execute action '{action_id}' because it has been REJECTED."
            )

        if action.status == ActionStatus.SUCCEEDED:
            raise InvalidStateTransitionError(
                f"Cannot execute action '{action_id}' because it has already SUCCEEDED."
            )

        if action.status != ActionStatus.APPROVED:
            raise InvalidStateTransitionError(
                f"Cannot execute action '{action_id}' in status '{action.status.value}'."
            )

        # Transition to EXECUTING
        action.status = ActionStatus.EXECUTING
        self._actions[action_id] = action
        self._update_action_status_in_graph(action)

        outcome_id = f"out_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            result = self.executor.execute(action)
            action.status = ActionStatus.SUCCEEDED
            action.executed_at = now_iso
            action.result = result

            outcome = Outcome(
                id=outcome_id,
                action_id=action.id,
                status=ActionStatus.SUCCEEDED,
                success=True,
                executed_at=now_iso,
                result=result,
            )
            self._write_outcome_to_graph(action, outcome)
            logger.info("Action execution succeeded: id=%s", action.id)
        except Exception as exc:
            action.status = ActionStatus.FAILED
            action.executed_at = now_iso
            action.error_message = str(exc)

            outcome = Outcome(
                id=outcome_id,
                action_id=action.id,
                status=ActionStatus.FAILED,
                success=False,
                executed_at=now_iso,
                result={"error": str(exc)},
            )
            self._write_outcome_to_graph(action, outcome)
            logger.error("Action execution failed: id=%s, err=%s", action.id, exc)

        self._actions[action_id] = action
        return action

    # -------------------------------------------------------------------------
    # Parameterized Graph Provenance Methods
    # -------------------------------------------------------------------------

    def _write_action_to_graph(self, action: Action) -> None:
        """Writes Action node and provenance links to FalkorDB."""
        try:
            graph = self.db_manager.get_graph(self.graph_name)

            # 1. Action Node
            create_query = """
            MERGE (a:Action {id: $id})
            SET a.type = $type,
                a.repo = $repo,
                a.target = $target,
                a.status = $status,
                a.reason = $reason,
                a.created_at = $created_at,
                a.arguments_json = $arguments_json
            """
            graph.query(
                create_query,
                {
                    "id": action.id,
                    "type": action.type.value if hasattr(action.type, "value") else str(action.type),
                    "repo": action.repo,
                    "target": action.target,
                    "status": action.status.value,
                    "reason": action.reason,
                    "created_at": action.created_at,
                    "arguments_json": json.dumps(action.arguments),
                },
            )

            # 2. Evidence Links: (:Action)-[:BASED_ON]->(:Evidence)
            for ev_id in action.evidence_ids:
                link_ev_query = """
                MATCH (a:Action {id: $action_id})
                MERGE (e:Evidence {id: $ev_id})
                MERGE (a)-[:BASED_ON]->(e)
                """
                graph.query(link_ev_query, {"action_id": action.id, "ev_id": ev_id})

            # 3. Claim Links: (:Action)-[:SUPPORTED_BY]->(:Claim)
            for cl_id in action.claim_ids:
                link_cl_query = """
                MATCH (a:Action {id: $action_id})
                MERGE (cl:Claim {id: $cl_id})
                MERGE (a)-[:SUPPORTED_BY]->(cl)
                """
                graph.query(link_cl_query, {"action_id": action.id, "cl_id": cl_id})

            # 4. Target Person Link: (:Action)-[:TARGETS]->(:Person)
            reviewer = action.arguments.get("reviewer")
            reviewer_email = action.arguments.get("reviewer_email")
            if reviewer or reviewer_email:
                link_person_query = """
                MATCH (a:Action {id: $action_id})
                MATCH (p:Person)
                WHERE p.name = $reviewer OR p.email = $reviewer_email
                MERGE (a)-[:TARGETS]->(p)
                """
                graph.query(
                    link_person_query,
                    {
                        "action_id": action.id,
                        "reviewer": reviewer or "",
                        "reviewer_email": reviewer_email or "",
                    },
                )
        except Exception as exc:
            logger.warning("FalkorDB provenance write error: %s", exc)

    def _update_action_status_in_graph(self, action: Action) -> None:
        """Updates status and timestamp on the Action node in FalkorDB."""
        try:
            graph = self.db_manager.get_graph(self.graph_name)
            query = """
            MATCH (a:Action {id: $id})
            SET a.status = $status,
                a.approved_at = $approved_at,
                a.rejected_at = $rejected_at,
                a.error_message = $error_message
            """
            graph.query(
                query,
                {
                    "id": action.id,
                    "status": action.status.value,
                    "approved_at": action.approved_at or "",
                    "rejected_at": action.rejected_at or "",
                    "error_message": action.error_message or "",
                },
            )
        except Exception as exc:
            logger.warning("FalkorDB status update error: %s", exc)

    def _write_outcome_to_graph(self, action: Action, outcome: Outcome) -> None:
        """Writes Outcome node and (:Action)-[:RESULTED_IN]->(:Outcome) link."""
        try:
            graph = self.db_manager.get_graph(self.graph_name)
            query = """
            MATCH (a:Action {id: $action_id})
            SET a.status = $status,
                a.executed_at = $executed_at,
                a.result_json = $result_json,
                a.error_message = $error_message
            MERGE (o:Outcome {id: $outcome_id})
            SET o.action_id = $action_id,
                o.status = $status,
                o.success = $success,
                o.executed_at = $executed_at,
                o.result_json = $result_json
            MERGE (a)-[:RESULTED_IN]->(o)
            """
            graph.query(
                query,
                {
                    "action_id": action.id,
                    "outcome_id": outcome.id,
                    "status": outcome.status.value,
                    "success": outcome.success,
                    "executed_at": outcome.executed_at,
                    "result_json": json.dumps(outcome.result),
                    "error_message": action.error_message or "",
                },
            )
        except Exception as exc:
            logger.warning("FalkorDB outcome write error: %s", exc)

    def _read_action_from_graph(self, action_id: str) -> Optional[Action]:
        """Reads Action node from FalkorDB."""
        try:
            graph = self.db_manager.get_graph(self.graph_name)
            query = """
            MATCH (a:Action {id: $id})
            OPTIONAL MATCH (a)-[:BASED_ON]->(e:Evidence)
            OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(cl:Claim)
            RETURN a.id, a.type, a.repo, a.target, a.status, a.reason,
                   a.created_at, a.approved_at, a.rejected_at, a.executed_at,
                   a.arguments_json, a.result_json, a.error_message,
                   collect(DISTINCT e.id) AS ev_ids,
                   collect(DISTINCT cl.id) AS cl_ids
            """
            res = graph.query(query, {"id": action_id})
            if res and res.result_set:
                row = res.result_set[0]
                if not row[0]:
                    return None

                args = json.loads(row[10]) if row[10] else {}
                result = json.loads(row[11]) if row[11] else None

                return Action(
                    id=str(row[0]),
                    type=ActionType(str(row[1])),
                    repo=str(row[2]),
                    target=str(row[3]),
                    reviewer=args.get("reviewer"),
                    status=ActionStatus(str(row[4])),
                    reason=str(row[5]),
                    created_at=str(row[6]),
                    approved_at=str(row[7]) if row[7] else None,
                    rejected_at=str(row[8]) if row[8] else None,
                    executed_at=str(row[9]) if row[9] else None,
                    arguments=args,
                    result=result,
                    error_message=str(row[12]) if row[12] else None,
                    evidence_ids=[str(x) for x in row[13] if x],
                    claim_ids=[str(x) for x in row[14] if x],
                )
        except Exception as exc:
            logger.warning("FalkorDB read action error: %s", exc)
        return None


# Global singleton
_action_service: Optional[ActionService] = None


def get_action_service() -> ActionService:
    global _action_service
    if _action_service is None:
        _action_service = ActionService()
    return _action_service
