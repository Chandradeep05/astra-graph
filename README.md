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

---

## Milestone 9: Multi-Contributor Intelligence + Impact-to-Reviewer Reasoning

### Selected Public Repository: `encode/databases`
- **Source**: `https://github.com/encode/databases.git`
- **Why Selected**: Manageable scale (335 commits), pure Python backend architecture, multiple active contributors with significant code overlap on core driver components (`databases/backends/postgres.py`, `databases/backends/mysql.py`, `databases/core.py`), producing genuine reviewer differentiation without artificial data.
- **Contributor Statistics**: 62 total contributors; primary contributors:
  - Tom Christie: 200 commits
  - George Bogodukhov: 21 commits
  - Amin Alaee: 21 commits
  - Ricardo Momm: 6 commits
  - Vadim Markovtsev: 6 commits

### Impact-to-Reviewer Workflow (`impact_reviewers`)
Anvaya connects historical blast-radius context with contributor lineage into a deterministic closed loop:
```text
Target File
    ↓ (CO_CHANGES)
Historical Co-Changing Files
    ↓ (AUTHORED / TOUCHES)
Relevant Contributors (Direct + Blast-Radius Commits)
    ↓ (Constraint Memory)
Filter Unavailable Reviewers
    ↓ (Deterministic Ranking)
Evidence-Backed Reviewer Candidates
    ↓ (EvidenceVerifier)
Verified Output
```

### Graph Relationships Used
- `(:File)-[:CO_CHANGES {count, last_ts}]->(:File)`: Historical co-change strength between files.
- `(:Person)-[:AUTHORED]->(:Commit)-[:TOUCHES]->(:File)`: Contributor commit touches and line churn.
- `(:Constraint {type: 'reviewer_unavailable', active: true})`: Persistent human decision memory in FalkorDB.

### Measured Example
**Target File**: `databases/backends/postgres.py`
- **Co-Changing Files**: `databases/backends/mysql.py` (45 shared commits), `databases/core.py` (38 shared commits), `tests/test_databases.py` (32 shared commits).
- **Candidates (Baseline)**:
  1. **Tom Christie**: 35 direct commits on target, 157 co-change commits across 5 related files.
  2. **George Bogodukhov**: 6 direct commits on target, 28 co-change commits across 5 related files.
  3. **Amin Alaee**: 4 direct commits on target, 16 co-change commits across 5 related files.
- **Persistent Memory Effect**: When a `reviewer_unavailable` constraint is recorded for Tom Christie in FalkorDB:
  - Tom Christie is excluded with structured constraint evidence.
  - **George Bogodukhov** is automatically elevated to the #1 recommended reviewer across fresh sessions.
  - If all candidates are constrained, Anvaya refuses cleanly with `NO_PATH`.

### Example API Request
```bash
GET /api/v1/impact-reviewers?file_path=databases/backends/postgres.py&repo=databases&limit=5
```

---

## Agent Workflow

```text
User request
   ↓
Graph tool selection
   ↓
Change impact
   ↓
Reviewer intelligence
   ↓
Evidence verification
   ↓
Human approval
   ↓
Action
   ↓
Graph outcome
   ↓
Persistent memory
```

### Architectural Separation of Concerns
- **What the LLM does**: Interprets natural language questions (e.g. extracts file paths and target repos) and generates strictly factual prose narrations from verified claims. The LLM is **never** permitted to generate Cypher, access FalkorDB directly, or invent facts, confidence scores, or rankings.
- **What FalkorDB does**: Serves as the operational single source of truth. FalkorDB executes graph traversals (`TOUCHES`, `AUTHORED`, `CO_CHANGES`, `APPLIES_TO`, `RESULTED_IN`), persists human constraints (`:Constraint`), records proposed actions (`:Action`), and persists execution outcomes (`:Outcome`).
- **What deterministic application code does**: Manages tool registration and input validation (`ToolRegistry`), executes parameterized Cypher queries, calculates deterministic candidate rankings, evaluates refusal conditions (`NO_PATH`, `OUT_OF_SCOPE`), enforces the human approval state machine (`ActionStatus`), and verifies evidence grounding (`EvidenceVerifier`).
- **Where human approval is required**: Actions (`request_review`) are proposed in `PROPOSED` state. Execution is strictly forbidden until an explicit human approval is received (`approve_action`). If the user rejects the proposal with a reason, structured memory records the constraint into FalkorDB, updating future decisions.

### Real Example: `encode/databases`
**User Request**:
> "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?"

1. **Tool Selection**: Deterministic routing selects `impact_reviewers` with `file_path="databases/backends/postgres.py"` and `repo="databases"`.
2. **Multi-Hop Traversal**: FalkorDB traverses `(f:File {path: 'databases/backends/postgres.py'})-[:CO_CHANGES]-(other:File)` and author lineages `(p:Person)-[:AUTHORED]->(:Commit)-[:TOUCHES]->(f:File)`.
3. **Change Impact**: Identifies co-changing files (`databases/backends/mysql.py` [45 co-changes], `databases/core.py` [38 co-changes], `tests/test_databases.py` [32 co-changes]).
4. **Reviewer Selection**: Ranks candidate reviewers (Tom Christie: 35 direct commits, 157 co-change commits; George Bogodukhov: 6 direct commits, 28 co-change commits).
5. **Evidence Verification**: Verifies 100% of claims against graph commit evidence.
6. **Action Proposal**: Creates `request_review` action for Tom Christie with status `PROPOSED`.
7. **Human Approval**: User approves action -> Sandbox executor records execution outcome -> Graph links `(:Action)-[:RESULTED_IN]->(:Outcome)`.
8. **Persistent Memory**: If the reviewer is rejected as unavailable, FalkorDB stores `(:Constraint {type: 'reviewer_unavailable', subject: 'Tom Christie', scope: 'repo:databases'})`. In a fresh session without in-process state, George Bogodukhov is recommended instead.

---

## Evidence-Backed Context Packages

> **Disclaimer**: *A Context Package is not a replacement for source code. It is a bounded relational context layer designed to reduce the amount of repository context an agent must reconstruct from scratch.*

Rather than dumping entire repositories into an LLM context window, Anvaya queries FalkorDB to synthesize a bounded, machine-readable **Context Package** (`ContextPackage`) representing the structural, historical, and organizational reality of a repository or target subject.

```text
Natural Language Query / API Request
                ↓
    FalkorDB Traversal & Clamping
(Modules, File Churn, Blast Radius, Constraints)
                ↓
      Evidence Construction
(Commit SHAs, Line Churn, Co-Changes, Timestamps)
                ↓
     EvidenceVerifier Check
 (100% Deterministic Grounding)
                ↓
 Structured ContextPackage + Narration
```

### Context Scopes

1. **Repository Context** (`subject_type: repository`):
   - Structural architecture derived from directory trees and co-change clusters (`backend`, `frontend`, `alembic`, `scripts`).
   - Top churn files by commit count and line additions/deletions.
   - Core historical contributors with line churn and last-active timestamps.
   - Cross-module dependency coupling and co-change bridges.
   - Active organizational constraints recorded in persistent graph memory.

2. **File Context** (`subject_type: file`):
   - Module membership and lineage.
   - Historical author lineage with direct commit counts and churn.
   - Blast-radius co-changing files (`CO_CHANGES`) across related modules.
   - Evidence-backed recommended reviewers with constraint exclusion filtering.

3. **Module Context** (`subject_type: module`):
   - Subsystem files, internal commit churn, and module-specific top contributors.

### Strict Trust Contract & Evidence Grounding

- **Application-Level Verification**: All factual claims in a `ContextPackage` are verified against attached `Evidence` objects via `EvidenceVerifier`.
- **Zero Hallucination**: No speculative confidence scores (e.g. "95% confidence") or subjective assertions ("expert reviewer") are permitted.
- **Fail-Closed Kill Switch**: If FalkorDB is unreachable, Anvaya fails closed with `refusal=True`, `reason_code=RefusalCode.NO_PATH`, and an explicit limitation note. It never falls back to an ungrounded LLM or unverified cache.
- **Explicit Bounds & Truncation Reporting**: Traversal depth is clamped to `[1, 5]`. If the graph contains more files or contributors than requested limits, `truncated=True` is set with detailed `truncation_reason`.

### Measured Examples

#### 1. Repository Context: `ARTH`
```bash
curl -X POST http://localhost:8000/api/v1/context \
  -H "Content-Type: application/json" \
  -d '{"repository": "arth", "depth": 3, "max_files": 10}'
```
- **Modules**: `backend` (104 files), `frontend` (82 files), `alembic` (3 files), `.` (12 files), `docs` (7 files), `.github` (5 files).
- **Freshness Snapshot**: Linked to `(:Sync)` snapshot `79595add24`, commit count `10`.
- **Top Contributor**: `chandradeep05` (97 commits, 21,957 lines added).
- **Verification**: `verified=True`, 5 verified claims, 0 rejected claims.

#### 2. File Context: `backend/app/engines/assistant/engine.py` (ARTH)
```bash
curl -X POST http://localhost:8000/api/v1/context \
  -H "Content-Type: application/json" \
  -d '{"repository": "arth", "subject": "backend/app/engines/assistant/engine.py"}'
```
- **Primary Author**: `chandradeep05` (21 commits touching file).
- **Blast Radius**: 19 co-changing files across `backend` and `.` modules.
- **Recommended Reviewer**: `chandradeep05` (unconstrained).

#### 3. Multi-Contributor File Context with Memory Constraints: `databases/backends/postgres.py` (Databases)
```bash
curl -X POST http://localhost:8000/api/v1/context \
  -H "Content-Type: application/json" \
  -d '{"repository": "databases", "subject": "databases/backends/postgres.py"}'
```
- **Primary Author**: Tom Christie (35 direct commits).
- **Blast Radius**: `databases/backends/mysql.py` (45 co-changes), `databases/core.py` (38 co-changes).
- **Persistent Constraint Effect**: When `Tom Christie` is marked `reviewer_unavailable` in FalkorDB, `is_constrained=True` is recorded in `ContributorContext`, Tom Christie is moved to `excluded_reviewers`, and `George Bogodukhov` is elevated to recommended reviewer.

---

## Track Alignment

### Track 01 — Agents That Act on Connected Data
Anvaya directly embodies Track 01:
- **Multi-Hop Graph Reasoning**: Answers require traversing multiple graph relationship hops: `File -> CO_CHANGES -> Related File -> TOUCHES -> Commit -> AUTHORED -> Person`.
- **Graph Queries as Agent Tools**: Application-owned tools (`reviewers_for`, `change_impact`, `impact_reviewers`) wrap parameterized Cypher queries with Pydantic validation contracts.
- **Explainable Evidence**: Every claim presented to the user is grounded in concrete evidence objects (`Evidence`) referencing commit SHAs, line churn, and timestamps.
- **Human-Approved Action**: The agent investigates and recommends, but requires human approval before execution (`PROPOSED` -> `APPROVED` -> `EXECUTING`).
- **Action Outcome Written Back to Graph**: Executed actions create `(:Outcome)` nodes linked directly to `(:Action)` in FalkorDB, providing complete provenance.

### Track 02 — Agent Memory and Coordination
Anvaya reinforces Track 02 through persistent engineering memory:
- **Persistent Graph Memory**: Human decisions and organizational constraints are persisted as `(:Constraint)` nodes in FalkorDB.
- **State Surviving Process Restart**: Constraints survive application restarts and fresh agent coordinator instantiation without in-memory caches.
- **Reviewer Availability Constraint**: Rejection of a reviewer creates a scoped constraint (`reviewer_unavailable` on `repo:databases`).
- **Changed Future Decision**: When the same question is asked in a fresh session, Anvaya excludes the unavailable contributor and deterministically selects the next evidence-supported reviewer.



