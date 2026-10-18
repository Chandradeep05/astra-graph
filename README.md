# ANVAYA

> Sanskrit: *connection, lineage* — Engineering Knowledge Graph & Evidence-Backed Agent on **FalkorDB**.

Anvaya constructs a real-time graph of repository lineage directly from Git history and GitHub interactions, unlocking deterministic multi-hop reasoning: who knows code, change risk, why changes occurred, and who should review pull requests.

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

1. **Ultra-Fast Graph Traversals**: Sub-millisecond multi-hop graph queries across commits, author lineages, and co-change clusters on Redis infrastructure.
2. **Deterministic Truth Layer**: All facts, commit counts, line additions, and blast-radius coupling metrics are computed directly by FalkorDB Cypher queries—never guessed or synthesized by an LLM.
3. **Persistent Engineering Memory**: Organizational constraints (e.g. reviewer unavailability) and action provenance (`:Action -> :Outcome`) are stored directly as graph nodes, surviving application restarts and fresh agent sessions.
4. **Fail-Closed Trust Contract**: If FalkorDB is offline or unreachable, Anvaya fails closed immediately with `refusal=True` and `reason_code=NO_PATH`. It never falls back to ungrounded LLMs or hallucinated context.

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
- **Graph Scale**: 213 files, 6 modules (`backend`, `frontend`, `alembic`, `docs`, `.github`, `.`), 10 historical sync commits.
- **Target File**: `backend/app/engines/assistant/engine.py`
  - Authored primarily by `chandradeep05` (21 commits touching file).
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

### 4. Run Verification Test Suite
```bash
pytest backend/tests -v
```

### 5. Launch the Application
```bash
uvicorn app.main:app --app-dir backend --reload --port 8000
```
Open **`http://localhost:8000/`** in your browser to access the Anvaya engineering product surface!

---

## Test Results

Anvaya maintains a comprehensive, deterministic test suite covering every milestone:

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

```text
============================= test session starts =============================
platform win32 -- Python 3.9.7, pytest-8.4.2
collected 143 items

backend/tests/test_actions.py .............                             [  9%]
backend/tests/test_agent.py .........                                   [ 15%]
backend/tests/test_context_package.py ....................              [ 29%]
backend/tests/test_evidence_verifier.py ..........                      [ 36%]
backend/tests/test_golden_workflow.py .......                            [ 41%]
backend/tests/test_health.py ....                                       [ 44%]
backend/tests/test_impact.py .................                          [ 55%]
backend/tests/test_ingest.py .......                                    [ 60%]
backend/tests/test_m12_frontend_integration.py ....................     [ 74%]
backend/tests/test_memory.py .............                              [ 83%]
backend/tests/test_multi_contributor.py ................                [ 95%]
backend/tests/test_reviewers.py .......                                 [100%]

============================ 143 passed in 27.38s ============================
```

---

## Known Limitations

- **Git-Only Lineage**: Architecture and coupling are derived strictly from Git directory trees, commit touches, and historical co-change co-occurrence. Dynamic runtime microservice calls, reflection, and network topologies are not modeled.
- **Historical Probability**: Blast-radius co-changes reflect statistical commit coupling, not abstract syntax tree (AST) execution paths.
- **Bounded Window**: Historical depth is intentionally bounded to avoid graph explosion on very large enterprise monorepos.
