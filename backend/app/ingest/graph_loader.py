from typing import List, Optional, Dict, Any
import datetime
import uuid
import logging
from pydantic import BaseModel

from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.ingest.models import GitCommit, IngestSummary

logger = logging.getLogger(__name__)


class CoChangePair(BaseModel):
    """
    Canonical representation of two files co-changing in git commit history.
    Guarantees lexical ordering (f1 < f2) and no self-pairs (f1 != f2).
    """
    file1: str
    file2: str
    count: int
    last_ts: str
    f1: Optional[str] = None
    f2: Optional[str] = None

    def model_post_init(self, __context):
        if not self.f1:
            self.f1 = self.file1
        if not self.f2:
            self.f2 = self.file2

    def __getitem__(self, item):
        return getattr(self, item)


def generate_co_change_pairs(commits: List[GitCommit]) -> List[Dict[str, Any]]:
    """
    Deterministically generates canonical co-change pairs from commit history:
    - Collects files touched by each commit.
    - Creates unordered file pairs canonically sorted (f1 < f2).
    - Excludes self-pairs (f1 == f2).
    - Increments count for each shared commit.
    - Updates last_ts with the most recent commit timestamp.
    """
    pairs_map: Dict[tuple, Dict[str, Any]] = {}
    for c in commits:
        if not c.touches:
            continue
        # Extract unique normalized file paths touched in this commit
        paths = sorted(list(set(t.path.replace("\\", "/").strip("/") for t in c.touches if t.path)))
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                p1, p2 = paths[i], paths[j]
                pair_key = (p1, p2)
                if pair_key not in pairs_map:
                    pairs_map[pair_key] = {
                        "f1": p1,
                        "f2": p2,
                        "file1": p1,
                        "file2": p2,
                        "count": 1,
                        "last_ts": c.timestamp,
                    }
                else:
                    pairs_map[pair_key]["count"] += 1
                    if c.timestamp > pairs_map[pair_key]["last_ts"]:
                        pairs_map[pair_key]["last_ts"] = c.timestamp

    return sorted(list(pairs_map.values()), key=lambda x: (x["f1"], x["f2"]))



class GitGraphLoader:
    """
    Loads parsed Git commits and relationships into FalkorDB
    using batched, idempotent Cypher queries.
    """
    generate_co_change_pairs = staticmethod(generate_co_change_pairs)

    def __init__(self, db_manager: Optional[FalkorDBManager] = None):
        self.db_manager = db_manager or get_falkor_manager()

    def ensure_schema(self, graph):
        """
        Idempotently creates indexes on entity keys for fast traversal and joins.
        """
        indexes = [
            "CREATE INDEX FOR (p:Person) ON (p.email)",
            "CREATE INDEX FOR (c:Commit) ON (c.sha)",
            "CREATE INDEX FOR (f:File) ON (f.path)",
            "CREATE INDEX FOR (m:Module) ON (m.path)",
            "CREATE INDEX FOR (r:Repository) ON (r.name)",
            "CREATE INDEX FOR (s:Sync) ON (s.id)",
        ]

        for idx_query in indexes:
            try:
                graph.query(idx_query)
            except Exception as exc:
                # FalkorDB raises error if index exists; safely ignore
                logger.debug("Index creation note for '%s': %s", idx_query, exc)

    def load_history(
        self,
        repo_name: str,
        commits: List[GitCommit],
        repo_path: str = "",
        graph_name: Optional[str] = None,
        batch_size: int = 50,
    ) -> IngestSummary:
        """
        Loads commits into FalkorDB in batches:
        - Person nodes and Commit nodes
        - AUTHORED edges
        - File and Module nodes
        - IN_MODULE and TOUCHES edges
        - Repository and Sync snapshot nodes
        """
        graph = self.db_manager.get_graph(graph_name)
        self.ensure_schema(graph)

        if not commits:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return IngestSummary(
                repo_name=repo_name,
                head_sha="",
                commit_count=0,
                person_count=0,
                file_count=0,
                module_count=0,
                sync_id=str(uuid.uuid4()),
                synced_at=now,
            )

        head_sha = commits[0].sha
        synced_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        sync_id = f"sync_{uuid.uuid4().hex[:12]}"

        # 1. Batch load Persons, Commits, and AUTHORED edges
        commit_query = """
        UNWIND $batch AS c
        MERGE (p:Person {email: c.author_email})
        ON CREATE SET p.name = c.author_name, p.login = c.author_email
        MERGE (cm:Commit {sha: c.sha})
        ON CREATE SET cm.ts = c.timestamp, cm.subject = c.subject
        MERGE (p)-[:AUTHORED]->(cm)
        """

        for i in range(0, len(commits), batch_size):
            chunk = commits[i : i + batch_size]
            batch_data = [
                {
                    "sha": c.sha,
                    "author_name": c.author_name,
                    "author_email": c.author_email,
                    "timestamp": c.timestamp,
                    "subject": c.subject,
                }
                for c in chunk
            ]
            graph.query(commit_query, {"batch": batch_data})

        # 2. Batch load Files, Modules, IN_MODULE, and TOUCHES edges
        touches_query = """
        UNWIND $batch AS t
        MATCH (cm:Commit {sha: t.sha})
        MERGE (f:File {path: t.path})
        MERGE (m:Module {path: t.module})
        MERGE (f)-[:IN_MODULE]->(m)
        MERGE (cm)-[tch:TOUCHES]->(f)
        ON CREATE SET tch.added = t.added, tch.deleted = t.deleted
        """

        all_touches = []
        for c in commits:
            for touch in c.touches:
                all_touches.append(
                    {
                        "sha": c.sha,
                        "path": touch.path,
                        "module": touch.module,
                        "added": touch.added,
                        "deleted": touch.deleted,
                    }
                )

        touch_batch_size = 200
        for i in range(0, len(all_touches), touch_batch_size):
            chunk = all_touches[i : i + touch_batch_size]
            graph.query(touches_query, {"batch": chunk})

        # 3. Batch load CO_CHANGES relationships
        co_change_pairs = self.generate_co_change_pairs(commits)
        if co_change_pairs:
            co_changes_query = """
            UNWIND $batch AS p
            MATCH (f1:File {path: p.f1})
            MATCH (f2:File {path: p.f2})
            MERGE (f1)-[cc:CO_CHANGES]->(f2)
            SET cc.count = p.count, cc.last_ts = p.last_ts
            """
            cc_batch_size = 500
            for i in range(0, len(co_change_pairs), cc_batch_size):
                chunk = co_change_pairs[i : i + cc_batch_size]
                graph.query(co_changes_query, {"batch": chunk})

        # 4. Create / Update Repository and Sync freshness snapshot
        repo_sync_query = """
        MERGE (r:Repository {name: $repo_name})
        ON CREATE SET r.path = $repo_path
        CREATE (s:Sync {
            id: $sync_id,
            head_sha: $head_sha,
            synced_at: $synced_at,
            commit_count: $commit_count,
            source: 'git'
        })
        MERGE (r)-[:SNAPSHOT]->(s)
        WITH r
        OPTIONAL MATCH (r)-[old_curr:CURRENT_AT]->(:Commit)
        DELETE old_curr
        WITH r
        MATCH (head:Commit {sha: $head_sha})
        MERGE (r)-[:CURRENT_AT]->(head)
        """
        graph.query(
            repo_sync_query,
            {
                "repo_name": repo_name,
                "repo_path": repo_path,
                "sync_id": sync_id,
                "head_sha": head_sha,
                "synced_at": synced_at,
                "commit_count": len(commits),
            },
        )

        # 5. Read back graph totals
        c_count = graph.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
        p_count = graph.query("MATCH (p:Person) RETURN count(p)").result_set[0][0]
        f_count = graph.query("MATCH (f:File) RETURN count(f)").result_set[0][0]
        m_count = graph.query("MATCH (m:Module) RETURN count(m)").result_set[0][0]
        cc_res = graph.query("MATCH ()-[c:CO_CHANGES]->() RETURN count(c)")
        cc_count = cc_res.result_set[0][0] if cc_res and cc_res.result_set else 0

        return IngestSummary(
            repo_name=repo_name,
            head_sha=head_sha,
            commit_count=c_count,
            person_count=p_count,
            file_count=f_count,
            module_count=m_count,
            co_changes_count=cc_count,
            sync_id=sync_id,
            synced_at=synced_at,
        )

