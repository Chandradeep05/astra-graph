# ANVAYA

> Sanskrit: *connection, lineage* — Graph-powered engineering intelligence for software teams, built on **FalkorDB**.

Anvaya constructs a real-time graph of repository lineage directly from Git history and GitHub interactions, unlocking deterministic multi-hop reasoning: who knows code, change risk, why changes occurred, and who should review pull requests.

---

## Non-Negotiable Trust Contract
1. **Facts come only from FalkorDB Cypher queries**, never invented by an LLM.
2. Every claim links to verifiable evidence IDs (commit SHAs, PR numbers, file paths).
3. If no supporting graph path exists, Anvaya refuses explicitly (`NO_PATH`, `LOW_EVIDENCE`, `STALE_DATA`, `OUT_OF_SCOPE`) before invoking any model.
4. If FalkorDB is offline, the core product refuses to guess.

---

## Milestone 1: FalkorDB Connection & Health Check

### Quickstart

1. **Start FalkorDB**:
   ```bash
   docker compose up -d
   ```

2. **Set up Python Virtual Environment**:
   ```bash
   python -m venv venv
   # Windows PowerShell:
   .\venv\Scripts\Activate.ps1
   # Linux/macOS:
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r backend/requirements.txt
   ```

4. **Run Health Verification Tests**:
   ```bash
   pytest backend/tests -v
   ```

5. **Start API Server**:
   ```bash
   uvicorn app.main:app --app-dir backend --reload --port 8000
   ```
   Check health endpoint: `http://localhost:8000/health`

---

## Milestone 8: Blast-Radius Intelligence & Historical Impact Evidence

### `CO_CHANGES` Graph Relationship
In FalkorDB, co-changes between files across historical commits are represented canonically as:
```cypher
(:File)-[:CO_CHANGES {count: int, last_ts: str}]->(:File)
```
- Canonical ordering: `f1.path < f2.path` prevents duplicate directional relationships.
- Traversal: Undirected matches `(f:File {path: $target})-[cc:CO_CHANGES]-(other:File)` query both directions seamlessly.
- Self-pairs are strictly excluded.

### `change_impact` Tool
Deterministic, application-owned tool registered in `tool_registry`:
- Input: `file_path` (or `file`), `repo` (default `arth`), `limit` (max 50, default 10), `max_evidence` (default 30)
- Output: `ChangeImpactResponse` with `impacted_files`, `related_people`, `connected_modules`, factual `signals`, verified `claims`, and `evidence` records.

### Example Query
```cypher
MATCH (f:File {path: 'backend/app/engines/assistant/engine.py'})-[cc:CO_CHANGES]-(other:File)
RETURN other.path, cc.count, cc.last_ts
ORDER BY cc.count DESC, cc.last_ts DESC
LIMIT 10
```

### Example API Request
```bash
GET /api/v1/impact?file_path=backend/app/engines/assistant/engine.py
```

### Trust Contract & Refusal Behavior
- **Unknown File**: If target file does not exist in the graph, Anvaya refuses immediately with `NO_PATH` without guessing.
- **Known File with No Co-Changes**: Returns a valid response with `impacted_files=[]` and a factual signal stating that no historical co-changes exist in the graph.
- **Evidence Verification**: All returned claims (`co_change_count`, `last_co_change`) are strictly verified against supporting commit evidence via `EvidenceVerifier`.
- **Kill Switch**: If FalkorDB is unreachable, the endpoint refuses safely without in-memory fallbacks.

