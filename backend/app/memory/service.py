from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.memory.models import (
    Constraint,
    ConstraintCreate,
    ConstraintNotFoundError,
    ConstraintType,
    InvalidConstraintError,
)

logger = logging.getLogger(__name__)


class MemoryService:
    """
    Application-owned Graph Memory Service for persistent engineering memory.
    Reads and writes directly to FalkorDB (:Constraint) nodes and relationships:
      (:Action)-[:RESULTED_IN]->(:Constraint)
      (:Constraint)-[:APPLIES_TO]->(:Person)

    Safety Invariant: The LLM is never allowed to author arbitrary Cypher.
    All operations are parameterized, deterministic, and verifiable.
    """

    def __init__(
        self,
        db_manager: Optional[FalkorDBManager] = None,
        graph_name: Optional[str] = None,
    ) -> None:
        self.db_manager = db_manager or get_falkor_manager()
        self.graph_name = graph_name

    def create_constraint(self, payload: ConstraintCreate) -> Constraint:
        """
        Creates and persists a structured constraint into FalkorDB.
        Links (:Action)-[:RESULTED_IN]->(:Constraint) if source_action_id is present.
        """
        if payload.type != ConstraintType.REVIEWER_UNAVAILABLE:
            raise InvalidConstraintError(
                f"Unsupported constraint type '{payload.type}'. Only '{ConstraintType.REVIEWER_UNAVAILABLE.value}' is supported."
            )

        if not payload.subject or not payload.subject.strip():
            raise InvalidConstraintError("Constraint subject cannot be empty.")

        constraint_id = f"constraint_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        constraint = Constraint(
            id=constraint_id,
            type=payload.type,
            subject=payload.subject.strip(),
            predicate=payload.predicate,
            value=payload.value,
            scope=payload.scope,
            created_at=now_iso,
            source_action_id=payload.source_action_id,
            active=payload.active,
        )

        graph = self.db_manager.get_graph(self.graph_name)

        # 1. Create Constraint node
        query_create = """
        MERGE (c:Constraint {id: $id})
        SET c.type = $type,
            c.subject = $subject,
            c.predicate = $predicate,
            c.value = $value,
            c.scope = $scope,
            c.created_at = $created_at,
            c.source_action_id = $source_action_id,
            c.active = $active
        """
        graph.query(
            query_create,
            {
                "id": constraint.id,
                "type": constraint.type.value if hasattr(constraint.type, "value") else str(constraint.type),
                "subject": constraint.subject,
                "predicate": constraint.predicate,
                "value": str(constraint.value),
                "scope": constraint.scope,
                "created_at": constraint.created_at,
                "source_action_id": constraint.source_action_id or "",
                "active": constraint.active,
            },
        )

        # 2. Link from source Action if specified: (:Action)-[:RESULTED_IN]->(:Constraint)
        if constraint.source_action_id:
            query_link_action = """
            MATCH (a:Action {id: $action_id})
            MATCH (c:Constraint {id: $constraint_id})
            MERGE (a)-[:RESULTED_IN]->(c)
            """
            graph.query(
                query_link_action,
                {"action_id": constraint.source_action_id, "constraint_id": constraint.id},
            )

        # 3. Link to target Person if subject matches a Person in the graph
        query_link_person = """
        MATCH (c:Constraint {id: $constraint_id})
        MATCH (p:Person)
        WHERE p.name = $subject OR p.email = $subject
        MERGE (c)-[:APPLIES_TO]->(p)
        """
        graph.query(
            query_link_person,
            {"constraint_id": constraint.id, "subject": constraint.subject},
        )

        logger.info(
            "Graph constraint persisted: id=%s, type=%s, subject=%s, scope=%s",
            constraint.id,
            constraint.type,
            constraint.subject,
            constraint.scope,
        )
        return constraint

    def get_active_constraints(
        self,
        type: Optional[str] = None,
        scope: Optional[str] = None,
        subject: Optional[str] = None,
    ) -> List[Constraint]:
        """
        Retrieves active constraints from FalkorDB.
        Enforces scope and subject matching directly via graph query.
        """
        graph = self.db_manager.get_graph(self.graph_name)

        query = """
        MATCH (c:Constraint)
        WHERE c.active = true
          AND ($type IS NULL OR c.type = $type)
          AND ($scope IS NULL OR c.scope = $scope)
          AND ($subject IS NULL OR c.subject = $subject)
        RETURN c.id, c.type, c.subject, c.predicate, c.value, c.scope,
               c.created_at, c.source_action_id, c.active
        ORDER BY c.created_at DESC
        """
        res = graph.query(
            query,
            {
                "type": type,
                "scope": scope,
                "subject": subject,
            },
        )

        constraints: List[Constraint] = []
        if res and res.result_set:
            for row in res.result_set:
                c_id = str(row[0])
                c_type = ConstraintType(str(row[1]))
                c_subject = str(row[2])
                c_pred = str(row[3])
                c_val = row[4]
                c_scope = str(row[5])
                c_created = str(row[6])
                c_src = str(row[7]) if row[7] else None
                c_active = bool(row[8])

                constraints.append(
                    Constraint(
                        id=c_id,
                        type=c_type,
                        subject=c_subject,
                        predicate=c_pred,
                        value=c_val,
                        scope=c_scope,
                        created_at=c_created,
                        source_action_id=c_src,
                        active=c_active,
                    )
                )

        return constraints

    def deactivate_constraint(self, constraint_id: str) -> bool:
        """Sets active=false on a constraint in FalkorDB."""
        graph = self.db_manager.get_graph(self.graph_name)
        query = """
        MATCH (c:Constraint {id: $id})
        SET c.active = false
        RETURN c.id
        """
        res = graph.query(query, {"id": constraint_id})
        return bool(res and res.result_set)

    def get_constraint(self, constraint_id: str) -> Optional[Constraint]:
        """Retrieves a single constraint by ID."""
        graph = self.db_manager.get_graph(self.graph_name)
        query = """
        MATCH (c:Constraint {id: $id})
        RETURN c.id, c.type, c.subject, c.predicate, c.value, c.scope,
               c.created_at, c.source_action_id, c.active
        """
        res = graph.query(query, {"id": constraint_id})
        if res and res.result_set:
            row = res.result_set[0]
            return Constraint(
                id=str(row[0]),
                type=ConstraintType(str(row[1])),
                subject=str(row[2]),
                predicate=str(row[3]),
                value=row[4],
                scope=str(row[5]),
                created_at=str(row[6]),
                source_action_id=str(row[7]) if row[7] else None,
                active=bool(row[8]),
            )
        return None


# Global singleton factory
_memory_service: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
