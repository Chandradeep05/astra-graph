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
