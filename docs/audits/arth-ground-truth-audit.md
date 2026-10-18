# Audit 3 & 3R: ARTH Ground-Truth, Graph Hygiene & Input Routing Remediation

## Executive Summary

- **Audit Date**: 2026-10-18
- **Audit Target**: ARTH Git History Ground Truth, FalkorDB Graph Hygiene, Agent Input Routing, Benchmark Topology
- **Initial Verdict**: `PASS WITH FINDINGS` (2 HIGH, 2 MEDIUM)
- **Final Remediated Verdict**: `PASS — ALL FINDINGS FULLY REMEDIATED`
- **Total Test Suite**: 171/171 passing (`pytest backend/tests`)
- **Production Graph Invariant**: `anvaya` production graph state verified 100% identical before and after test suite execution.

---

## 1. Repository Ground-Truth Grounding

Anvaya operates on two canonical, verified Git repositories ingested into FalkorDB:

| Metric | ARTH (`data/repos/arth`) | encode/databases (`data/repos/databases`) |
| :--- | :--- | :--- |
| **Ingested Commits** | 97 | 335 |
| **Tracked Files** | 213 | 53 |
| **File Touches** | 771 | 1,024 |
| **Unique Contributors** | 1 (`chandradeep05`) | 62 (`tomchristie`, `gvbgduh`, etc.) |
| **Directed `CO_CHANGES` Edges** | 8,118 (16,236 undirected) | 497 (994 undirected) |
| **HEAD SHA** | `79595add24d7e561d28be4fe66af3fa2c17b933d` | `8c6ff1d2931b99781604a8b789965a77f3a0a38f` |
| **Primary Evaluation Purpose** | Deep co-change topology, blast-radius reasoning, repository context packages | Multi-contributor ownership, competitive reviewer ranking, constraint memory |

---

## 2. Findings & Remediations

### FINDING-A3-001 (HIGH) — Integration Tests Mutated Production FalkorDB Graph
- **Severity**: HIGH
- **Status**: `FIXED`
- **Root Cause**: `backend/tests/test_ingest.py::test_ingest_api_endpoint` performed a POST request with `max_commits=10` against the default graph (`anvaya`), resetting `Sync.commit_count = 10` and degrading co-change frequencies (e.g. `internal.py` <-> `alerts.py` dropped from 5 to 1).
- **Remediation**:
  1. Configured strict test graph isolation: `pytest` now executes against `anvaya_test` by default.
  2. Implemented `backend/tests/conftest.py` with session-scoped autouse fixture `isolate_and_protect_production_graph`:
     - Snapshots `anvaya` graph signature before any test runs.
     - Seeds `anvaya_test` idempotently with canonical ground truth.
     - Resets application service singletons between tests.
     - Asserts `anvaya_before == anvaya_after` at session teardown.
  3. Added `graph_name: Optional[str] = None` to `IngestRequest` and `backend/app/api/routes/ingest.py`, allowing test callers to supply isolated target graphs.
  4. Updated `test_ingest_api_endpoint` to use an isolated ephemeral test graph (`test_endpoint_ingest_graph`) with deterministic deletion in a `finally` block.
  5. Canonical `anvaya` graph restored and verified with 97 commits, 213 files, 8,118 CO_CHANGES edges, and `internal.py` <-> `alerts.py` co-change count = 5.

---

### FINDING-A3-002 (HIGH) — ARTH Single-Contributor Reality & Benchmark Partitioning
- **Severity**: HIGH (Methodological / Evaluation)
- **Status**: `DOCUMENTED / ARCHITECTURAL BENCHMARK STRATEGY`
- **Root Cause**: ARTH's entire Git history (97 commits) was authored by a single contributor (`chandradeep05`). Therefore, competitive multi-author reviewer scoring cannot be demonstrated on ARTH alone without synthetic fabrication (which would violate trust principles).
- **Remediation**:
  1. Explicitly documented the benchmark strategy:
     - **ARTH** serves as the primary benchmark for blast-radius calculation, co-change graph traversal, deep file and module context package generation, and single-author lineage.
     - **encode/databases** serves as the primary benchmark for multi-contributor reviewer ranking, ownership scoring, tenure weighting, recency decay, and persistent reviewer unavailability constraints.
  2. Created regression test `test_a3_002_arth_and_databases_ground_truth_characteristics` asserting ground-truth contributor characteristics for both repositories.

---

### FINDING-A3-003 (MEDIUM) — Unknown Context Subjects Silently Default to Entire Repository
- **Severity**: MEDIUM
- **Status**: `FIXED`
- **Root Cause**: In `TemplateProvider.select_tool`, when a context request (e.g. `"Give me context for nonexistent_module"`) lacked a dot for file matching and did not contain the token `"module"`, it defaulted to `subject=target_repo, subject_type="repository"`, silently returning the entire repository context instead of failing closed.
- **Remediation**:
  1. Updated `TemplateProvider.select_tool` with explicit subject extraction (`context for <subject>`, `understand <subject>`, `overview of <subject>`).
  2. If a non-generic subject is requested, routes to `context_package` with `subject=candidate, subject_type="module"`.
  3. If the module or file does not exist in FalkorDB, `ContextPackageService` queries the graph and returns `refusal=True` with `reason_code=RefusalCode.NO_PATH`, maintaining strict fail-closed integrity.
  4. Created regression tests verifying that nonexistent subjects refuse with `NO_PATH`, while valid modules (`backend`) and repositories (`arth`) return complete context packages.

---

### FINDING-A3-004 (MEDIUM) — TemplateProvider File Regex Excluded Leading-Dot Files
- **Severity**: MEDIUM
- **Status**: `FIXED`
- **Root Cause**: `re.search(r"([a-zA-Z0-9_\-\./]+\.[a-zA-Z0-9_\-]+)", q)` required alphanumeric characters before the dot, completely missing leading-dot filenames such as `.gitignore`, `.env.example`, `.env`, and workflow files. Additionally, queries containing `.github/workflows/deploy.yml` triggered the destructive/execution keyword filter on `"deploy"`.
- **Remediation**:
  1. Updated file extraction regex to:
     `r"(?:^|[\s\"'`])(\.?[\w\-./]*\.[a-zA-Z0-9_\-]+)(?:[\s\"'`?,!;:.]|$)"`
     Correctly extracts `.gitignore`, `.env.example`, `.github/workflows/deploy.yml`, and standard source paths without trailing punctuation.
  2. Adjusted destructive command check in `TemplateProvider` so queries identified as reviewer inquiries (`is_reviewer_query`) are never falsely rejected as destructive/deploy commands.
  3. Created regression tests verifying end-to-end extraction and coordinator resolution for dotfiles.

---

## 3. Test Suite Verification

```text
============================= test session starts =============================
platform win32 -- Python 3.9.7, pytest-8.4.2, pluggy-1.6.0
collected 171 items

backend\tests\test_actions.py .............                              [  7%]
backend\tests\test_agent.py .............                                [ 15%]
backend\tests\test_audit2r_remediation.py ............                   [ 22%]
backend\tests\test_audit3r_remediation.py .........                      [ 27%]
backend\tests\test_context_package.py ....................               [ 39%]
backend\tests\test_evidence_verifier.py ............                     [ 46%]
backend\tests\test_golden_workflow.py ..........                         [ 52%]
backend\tests\test_health.py ...                                         [ 53%]
backend\tests\test_impact.py ...............                             [ 62%]
backend\tests\test_ingest.py .....                                       [ 65%]
backend\tests\test_m12_frontend_integration.py ....................      [ 77%]
backend\tests\test_memory.py ........                                    [ 81%]
backend\tests\test_multi_contributor.py .................                [ 91%]
backend\tests\test_repo_isolation.py .......                             [ 95%]
backend\tests\test_reviewers.py .......                                  [100%]

============================ 171 passed in 43.00s =============================
```

## 4. Production Graph Invariance Proof

```text
Pre-test run state of 'anvaya':
- Commits: 432 (97 ARTH + 335 databases)
- Files: 266 (213 ARTH + 53 databases)
- Latest ARTH Sync: ['79595add24d7e561d28be4fe66af3fa2c17b933d', 97]
- Co-changes count: 8615
- internal.py <-> alerts.py co-change count: 5

Post-test run state of 'anvaya':
- Commits: 432 (97 ARTH + 335 databases)
- Files: 266 (213 ARTH + 53 databases)
- Latest ARTH Sync: ['79595add24d7e561d28be4fe66af3fa2c17b933d', 97]
- Co-changes count: 8615
- internal.py <-> alerts.py co-change count: 5
```
Delta: 0 nodes, 0 edges, 0 property mutations. Production graph invariance verified.
