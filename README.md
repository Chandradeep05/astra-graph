# ANVAYA

> Sanskrit: *connection, lineage* — Engineering Knowledge Graph & Evidence-Backed Agent on **FalkorDB**.

Anvaya constructs an operational knowledge graph of repository lineage directly from Git history, unlocking deterministic multi-hop reasoning: who knows code, change risk, why changes occurred, who should review pull requests, and human-approved action lifecycles.

---

## What Anvaya Does

Anvaya transforms software repositories into an operational knowledge graph on **FalkorDB**. Rather than feeding raw source code into an ungrounded LLM or relying on fuzzy vector embeddings, Anvaya executes multi-hop graph traversals across commit history, file co-changes, and organizational memory to solve core engineering coordination challenges:

```text
User Question
      ↓
Agent Intent Understanding & Tool Routing
      ↓
FalkorDB Multi-Hop Graph Traversal
(:File)-[:CO_CHANGES]-(:File)<-[:TOUCHES]-(:Commit)<-[:AUTHORED]-(:Person)
      ↓
Deterministic Evidence Verification (EvidenceVerifier)
      ↓
Verified Narration & Structured Context
      ↓
Proposed Action (request_review)
      ↓
Human Approval Gate (Mandatory)
      ↓
Execution & Graph Provenance ((:Action)-[:RESULTED_IN]->(:Outcome))
      ↓
Persistent Graph Memory ((:Constraint))
      ↓
Fresh Session → Changed Future Recommendation
```

---

## Why FalkorDB

FalkorDB is the foundational, indispensable operational core of Anvaya:

1. **Fast Graph Traversals**: Single-digit to tens-of-milliseconds graph queries (typically 9.8 ms – 65 ms on local benchmarks) across commits, author lineages, and co-change clusters.
2. **Deterministic Truth Layer**: All facts, commit counts, line churn metrics, and blast-radius coupling are computed directly by FalkorDB Cypher queries. LLMs do not author evidence-backed factual claims; deterministic graph queries and verification establish the factual basis.
3. **Persistent Engineering Memory**: Organizational constraints (e.g. reviewer unavailability) and action provenance (`:Action -> :Outcome`) are stored directly as graph nodes, surviving application restarts and fresh agent sessions.
4. **Fail-Closed Trust Contract**: If FalkorDB is offline or unreachable, Anvaya fails closed immediately with `refusal=True` and `reason_code=NO_PATH`. When graph evidence is unavailable, Anvaya refuses rather than generating an unsupported answer.

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       JUDGE-FACING PRODUCT SURFACE                          │
│     Ask / Investigate  │  Interactive Graph Explorer  │  Context Packages   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ HTTP / JSON
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                              FASTAPI BACKEND                                │
│   /api/v1/ask  │  /api/v1/context  │  /api/v1/graph  │  /api/v1/actions     │
└───────┬──────────────────────────────┬──────────────────────────────┬───────┘
        │                              │                              │
┌───────▼──────────────┐   ┌───────────▼────────────┐   ┌─────────────▼───────┐
│     AGENT LAYER      │   │   DETERMINISTIC TOOLS  │   │  TRUST & VERIFIER   │
│ - TemplateProvider   │   │ - reviewers_for        │   │ - ToolRegistry      │
│ - Groq / Gemini LLM  │   │ - change_impact        │   │ - EvidenceVerifier  │
│ - Safe Tool Routing  │   │ - impact_reviewers     │   │ - Refusal Handling  │
│ - Verified Narrator  │   │ - context_package      │   │ - Parameter Clamping│
└───────┬──────────────┘   └───────────┬────────────┘   └─────────────┬───────┘
        │                              │                              │
        └──────────────────────────────┼──────────────────────────────┘
                                       │ Parameterized Cypher
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                           FALKORDB KNOWLEDGE GRAPH                          │
│   (:Repository) ──[:SNAPSHOT]──► (:Sync)                                    │
│   (:File) ───────[:IN_MODULE]──► (:Module)                                  │
│   (:Commit) ─────[:TOUCHES]────► (:File)                                    │
│   (:Person) ─────[:AUTHORED]───► (:Commit)                                  │
│   (:File) ───────[:CO_CHANGES]─► (:File)                                    │
│   (:Action) ─────[:RESULTED_IN]► (:Outcome)                                 │
│   (:Constraint) ─[:APPLIES_TO]─► (:Person)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Graph Model

Anvaya models software development as a typed, property-rich knowledge graph:

### Nodes
- `(:Repository {name: str, default_branch: str})`
- `(:Module {path: str, name: str})`
- `(:File {path: str, name: str, extension: str})`
- `(:Commit {sha: str, committed_at: str, ts: str})`
- `(:Person {name: str, email: str})`
- `(:Sync {id: str, head_sha: str, synced_at: str, commit_count: int})`
- `(:Action {id: str, type: str, status: str, reviewer: str, target: str, repo: str})`
- `(:Outcome {id: str, status: str, success: bool, created_at: str})`
- `(:Constraint {id: str, type: str, subject: str, scope: str, active: bool})`

### Relationships
- `(:Repository)-[:SNAPSHOT]->(:Sync)`: Ingestion snapshot provenance.
- `(:File)-[:IN_MODULE]->(:Module)`: Module and directory tree membership.
- `(:Commit)-[:TOUCHES {added: int, deleted: int}]->(:File)`: File modifications and line churn per commit.
- `(:Person)-[:AUTHORED]->(:Commit)`: Contributor commit authorship.
- `(:File)-[:CO_CHANGES {count: int, last_ts: str}]->(:File)`: Historical co-change coupling between files.
- `(:Action)-[:RESULTED_IN]->(:Outcome)`: Sandbox execution audit trail.
- `(:Constraint)-[:APPLIES_TO]->(:Person)`: Persistent organizational memory constraint.

---

## Agent Workflow

Anvaya enforces a strict operational separation between orchestrators and source-of-truth knowledge:

```text
1. USER REQUEST
   "I'm going to modify databases/backends/postgres.py. What could this affect, who should review it, and why?"
        ↓
2. DETERMINISTIC TOOL ROUTING
   Agent extracts parameters -> selects 'impact_reviewers' tool from ToolRegistry.
        ↓
3. MULTI-HOP GRAPH INVESTIGATION
   FalkorDB traverses:
   postgres.py --[:CO_CHANGES]--> mysql.py --[:TOUCHED_BY]--> Commit --[:AUTHORED]--> Person
        ↓
4. EVIDENCE VERIFICATION
   EvidenceVerifier checks 100% of claims against attached Evidence records.
        ↓
5. VERIFIED NARRATION
   Factual explanation generated strictly from verified claims without hallucinated confidence scores.
        ↓
6. ACTION PROPOSAL
   Creates request_review action with status PROPOSED (human approval required).
        ↓
7. HUMAN DECISION
   Human reviews proposal -> approves or rejects with reason.
        ↓
8. SANDBOX EXECUTION & MEMORY
   - If approved: executes notification and writes (:Action)-[:RESULTED_IN]->(:Outcome) to FalkorDB.
   - If rejected as unavailable: writes (:Constraint {type: 'reviewer_unavailable'}) to FalkorDB.
```

---

## Context Packages

> **Disclaimer**: *A Context Package is not a replacement for source code. It is a bounded relational context layer designed to reduce the amount of repository context an agent must reconstruct from scratch.*

Rather than dumping raw source files into an LLM context window, Anvaya synthesizes a bounded, machine-readable **Context Package** (`ContextPackage`) representing the structural, historical, and organizational reality of a repository or target subject:

- **Repository Context** (`subject_type: repository`): Architecture modules, file churn hotspots, core contributors, cross-module dependencies, and persistent constraints.
- **File Context** (`subject_type: file`): Module membership, author lineage, co-change blast radius, and recommended reviewers.
- **Module Context** (`subject_type: module`): Subsystem file containment, churn, and module-specific top authors.
- **Bounds Enforced**: Traversal depth is clamped to `[1, 5]`. Results exceeding file or contributor limits set `truncated=True` with explicit `truncation_reason`.

---

## Persistent Memory

Anvaya's persistent memory directly addresses **Track 02 (Agent Memory and Coordination)**:

1. **State Surviving Process Restarts**: Constraints are persisted as graph nodes in FalkorDB, not stored in ephemeral process memory.
2. **Changed Future Decisions**:
   - **Session 1**: User asks who should review `postgres.py`. Anvaya recommends **Tom Christie** (#1 by historical commits).
   - **Human Decision**: User rejects Tom Christie with reason *"Reviewer on sabbatical / unavailable"*. FalkorDB records a `reviewer_unavailable` constraint.
   - **Session 2 (Fresh Process)**: The exact same question is asked in a new session. Anvaya discovers the active constraint in FalkorDB, excludes Tom Christie, and elevates **George Bogodukhov** to #1!

---

## Human Approval

Autonomous AI agents must not make external repository mutations without human consent:

- **State Machine**: `PROPOSED -> APPROVED -> EXECUTING -> SUCCEEDED` (or `REJECTED`).
- **Strict Enforcement**: Attempting to execute an action in `PROPOSED` state triggers `InvalidStateTransitionError: Human approval is mandatory before action execution`.
- **Audit Provenance**: Every approved execution writes an `(:Outcome)` node directly linked to `(:Action)` in FalkorDB with execution timestamp and sandbox validation.

---

## Graph Visualization

Anvaya provides an interactive, visual engineering tool surface:

1. **Interactive Graph Explorer** (`#tab-graph`):
   - Powered by **Cytoscape.js** (works 100% offline via local vendor bundle).
   - Color-coded node badges: Repository (purple), Module (indigo), File (blue), Commit (amber), Person (emerald), Constraint (rose), Action (cyan), Outcome (teal).
   - Force-directed (`cose`), concentric, and hierarchical layouts.
   - Real-time **Node Inspector** displaying complete structured properties for any clicked node.
2. **Evidence Path Tracing** (`/api/v1/graph/evidence-path`):
   - Visually renders the exact multi-hop graph path supporting any recommendation:
     `[File: postgres.py] ──[ CO_CHANGES ]──► [File: mysql.py] ──[ TOUCHES ]──► [Commit: 4d87d76c] ──[ AUTHORED ]──► [Person: Tom Christie]`

---

## Refusal / Safety

Trust requires knowing when to refuse rather than hallucinating:

- **First-Class UI Refusal States**:
  - `NO_PATH`: Target file or module does not exist in graph lineage.
  - `LOW_EVIDENCE`: Insufficient historical commits or co-changes to justify an action.
  - `STALE_DATA`: Graph snapshot exceeds staleness window.
  - `OUT_OF_SCOPE`: Malicious prompt injection or arbitrary Cypher attempts.
- **Fail-Closed Kill Switch**: If FalkorDB connection health fails, all endpoints refuse safely.
- **Zero Cypher Injection**: All database queries use strictly parameterized Cypher maps.

---

## ARTH Example

- **Repository**: `chandradeep05/ARTH` (Personal trading assistant).
- **Graph Scale**: 97 commits (HEAD: `79595add24d7e561d28be4fe66af3fa2c17b933d`), 213 historical files, 771 file touches, 49,170 additions, 8,381 deletions, 8,118 directed `CO_CHANGES` edges across 6 modules (`backend`, `frontend`, `alembic`, `docs`, `.github`, `.`).
- **Target File**: `backend/app/engines/assistant/engine.py`
  - Authored by `chandradeep05` (21 commits touching file).
  - Blast Radius: 19 historical co-changing files across `backend` and `.` modules.
  - Context Package: Complete architectural breakdown with 100% verified claims and zero rejected claims.

---

## Encode/Databases Example

- **Repository**: `encode/databases` (Public asynchronous database library).
- **Graph Scale**: 335 commits, 62 contributors, 53 tracked files.
- **Target File**: `databases/backends/postgres.py`
  - Co-Changes: `databases/backends/mysql.py` (45 shared commits), `databases/core.py` (38 shared commits), `tests/test_databases.py` (32 shared commits).
  - Baseline Candidates:
    1. **Tom Christie**: 35 direct commits, 157 co-change commits across 5 related files.
    2. **George Bogodukhov**: 6 direct commits, 28 co-change commits across 5 related files.
    3. **Amin Alaee**: 4 direct commits, 16 co-change commits across 5 related files.
  - Persistent Memory Effect: Marking Tom Christie unavailable elevates George Bogodukhov across fresh sessions.

---

## Running Locally

### 1. Prerequisites
- Docker & Docker Compose
- Python 3.9+
- Git

### 2. Start FalkorDB
```bash
docker compose up -d
```

### 3. Setup Python Virtual Environment
```bash
python -m venv venv

# Windows PowerShell:
.\venv\Scripts\Activate.ps1

# Linux / macOS:
source venv/bin/activate

pip install -r backend/requirements.txt
```

### 4. Obtain Sample Repositories (Public Benchmark Data)
Anvaya evaluates on two public open-source repositories (cloned into `data/repos/`, which is gitignored):
```bash
# Windows PowerShell / Bash:
mkdir -p data/repos
git clone https://github.com/Chandradeep05/ARTH.git data/repos/arth
git clone https://github.com/encode/databases.git data/repos/databases
```

### 5. Ingest Repositories into FalkorDB
Populate the canonical `anvaya` production graph using the provided ingestion script:
```bash
python scripts/ingest_sample_data.py
```
*(Alternatively, you can trigger ingestion via the FastAPI endpoint once the server is started: `POST /api/v1/ingest` with `{"repo_path": "data/repos/arth", "repo_name": "arth"}`.)*

### 6. Run Verification Test Suite
Execute the full automated test suite (runs in complete isolation against `anvaya_test` without mutating the canonical `anvaya` production graph):
```bash
pytest backend/tests -v
```

### 7. Launch the Application
```bash
uvicorn app.main:app --app-dir backend --reload --port 8000
```
Open **`http://localhost:8000/`** in your browser to access the Anvaya engineering product surface!

---

## Test Results

Anvaya maintains a comprehensive, deterministic test suite covering every milestone and audit remediation:

- `test_health.py`: FalkorDB connection, ping, and health check.
- `test_ingest.py`: Git-only ingestion pipeline, commit touches, and file containment.
- `test_reviewers.py`: Reviewer candidate intelligence, commit ranking, and line churn.
- `test_evidence_verifier.py`: Evidence verification rules, forbidden term detection, and refusal handling.
- `test_agent.py`: Safe tool routing, LLM providers, and template narration.
- `test_actions.py`: Action lifecycle state machine, human approval gate, and sandbox execution.
- `test_memory.py`: Persistent constraint storage, retrieval, and reviewer exclusion.
- `test_impact.py`: Blast-radius intelligence and historical `:CO_CHANGES` evidence.
- `test_multi_contributor.py`: Multi-contributor impact-to-reviewer reasoning on `encode/databases`.
- `test_golden_workflow.py`: End-to-end Investigate -> Explain -> Propose -> Act lifecycle.
- `test_context_package.py`: Bounded repository, file, and module context packages.
- `test_m12_frontend_integration.py`: Web UI serving, Cytoscape graph exploration, evidence path tracing, and memory reset.
- `test_repo_isolation.py`: Cross-repository graph isolation and repository-scoped file identity.
- `test_audit2r_remediation.py`: Trust boundary enforcement, action graph provenance, and fail-closed gates.
- `test_audit3r_remediation.py`: Test graph isolation (`anvaya_test`), dotfile extraction, and context routing.

```text
============================= test session starts =============================
platform win32 -- Python 3.9.7, pytest-8.4.2
collected 171 items

backend/tests/test_actions.py .............                             [  7%]
backend/tests/test_agent.py .............                               [ 15%]
backend/tests/test_audit2r_remediation.py ............                  [ 22%]
backend/tests/test_audit3r_remediation.py .........                     [ 27%]
backend/tests/test_context_package.py ....................              [ 39%]
backend/tests/test_evidence_verifier.py ............                    [ 46%]
backend/tests/test_golden_workflow.py ..........                        [ 52%]
backend/tests/test_health.py ...                                        [ 53%]
backend/tests/test_impact.py ...............                            [ 62%]
backend/tests/test_ingest.py .....                                      [ 65%]
backend/tests/test_m12_frontend_integration.py ....................     [ 77%]
backend/tests/test_memory.py ........                                   [ 81%]
backend/tests/test_multi_contributor.py .................               [ 91%]
backend/tests/test_repo_isolation.py .......                            [ 95%]
backend/tests/test_reviewers.py .......                                 [100%]

============================ 171 passed in 33.02s ============================
```

---

## Known Limitations

- **Git-Only Lineage**: Architecture and coupling are derived strictly from Git directory trees, commit touches, and historical co-change co-occurrence. Dynamic runtime microservice calls, reflection, and network topologies are not modeled.
- **Historical Probability**: Blast-radius co-changes reflect statistical commit coupling, not abstract syntax tree (AST) execution paths.
- **Bounded Window**: Historical depth is intentionally bounded to avoid graph explosion on very large enterprise monorepos.
- **Sandbox Action Execution**: Review request actions are executed and provenanced in FalkorDB within a simulated sandbox environment (`sandbox: true`); live automated mutation of external GitHub pull-request review assignments is intentionally decoupled to ensure human oversight.
- **Local Benchmark Measurements**: Graph query latencies (typically 9.8 ms – 65 ms on local benchmarks) reflect local FalkorDB execution. In our local benchmark, graph-backed reviewer retrieval (`reviewers_for`) completed in **9.83 ms** (approx. 34× faster than parsing raw Git history at **333.61 ms**). Latencies vary by repository size, traversal depth, and host hardware.
