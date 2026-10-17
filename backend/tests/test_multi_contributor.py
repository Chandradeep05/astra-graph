import pytest
import os
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.ingest.git_parser import parse_git_history
from app.tools.impact_reviewers import impact_reviewers
from app.tools.reviewers import reviewers_for
from app.tools.registry import tool_registry, InvalidToolArgumentsError
from app.memory.service import MemoryService
from app.memory.models import ConstraintCreate, ConstraintType
from app.evidence.models import RefusalCode

client = TestClient(app)
REPO_PATH = "data/repos/databases"
REPO_NAME = "databases"
TARGET_FILE = "databases/backends/postgres.py"


# ---------------------------------------------------------------------------
# REPOSITORY & CONTRIBUTOR TESTS (Parts 1, 2, 3)
# ---------------------------------------------------------------------------

def test_repo_has_multiple_meaningful_contributors():
    """Verify the selected public repository contains at least 3 meaningful contributors."""
    if not os.path.exists(REPO_PATH):
        pytest.skip(f"Repository not found at {REPO_PATH}")

    commits = parse_git_history(REPO_PATH, max_commits=2000)
    assert len(commits) == 335

    author_commits = {}
    for c in commits:
        author_commits[c.author_name] = author_commits.get(c.author_name, 0) + 1

    # Verify at least 3 contributors have substantial history (> 15 commits)
    meaningful = [name for name, count in author_commits.items() if count >= 15]
    assert len(meaningful) >= 3
    assert "Tom Christie" in meaningful
    assert "George Bogodukhov" in meaningful
    assert "Amin Alaee" in meaningful


def test_ingestion_creates_multiple_person_nodes():
    """Verify that FalkorDB contains multiple Person nodes for the databases repo."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    graph = db.get_graph("anvaya")
    res = graph.query("MATCH (p:Person) RETURN count(p)")
    assert res.result_set[0][0] >= 30


def test_contributor_commit_counts_match_raw_git():
    """Verify that contributor commit counts in FalkorDB match independent Git calculation."""
    if not os.path.exists(REPO_PATH):
        pytest.skip(f"Repository not found at {REPO_PATH}")

    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # 1. Raw Git calculation
    commits = parse_git_history(REPO_PATH, max_commits=2000)
    git_counts = {}
    for c in commits:
        git_counts[c.author_email] = git_counts.get(c.author_email, 0) + 1

    # 2. FalkorDB query for top contributors
    graph = db.get_graph("anvaya")
    for email in ["tom@tomchristie.com", "gvbgduh@gmail.com", "mohammadamin.alaee@gmail.com"]:
        q = """
        MATCH (p:Person {email: $email})-[:AUTHORED]->(c:Commit)
        RETURN count(c)
        """
        graph_count = graph.query(q, {"email": email}).result_set[0][0]
        assert graph_count == git_counts[email], f"Commit count mismatch for {email}"


def test_contributor_file_relationships_match_raw_git():
    """Verify contributor authorship counts on TARGET_FILE match independent Git log."""
    if not os.path.exists(REPO_PATH):
        pytest.skip(f"Repository not found at {REPO_PATH}")

    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # 1. Raw Git ground truth for TARGET_FILE
    commits = parse_git_history(REPO_PATH, max_commits=2000)
    git_authors = {}
    for c in commits:
        for t in c.touches:
            if t.path == TARGET_FILE:
                git_authors[c.author_email] = git_authors.get(c.author_email, 0) + 1

    # 2. FalkorDB query
    graph = db.get_graph("anvaya")
    q = """
    MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHES]->(f:File {path: $target})
    RETURN p.email, count(DISTINCT c)
    """
    res = graph.query(q, {"target": TARGET_FILE})
    graph_authors = {r[0]: r[1] for r in res.result_set}

    for email, expected_count in git_authors.items():
        assert graph_authors.get(email) == expected_count, f"File touch mismatch for {email}"


def test_co_changes_match_raw_git():
    """Verify CO_CHANGES counts with TARGET_FILE match independent raw Git history."""
    if not os.path.exists(REPO_PATH):
        pytest.skip(f"Repository not found at {REPO_PATH}")

    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # 1. Raw Git ground truth co-changes
    commits = parse_git_history(REPO_PATH, max_commits=2000)
    git_co_changes = {}
    for c in commits:
        paths = {t.path.replace("\\", "/").strip("/") for t in c.touches if t.path}
        if TARGET_FILE in paths:
            for p in paths:
                if p != TARGET_FILE:
                    git_co_changes[p] = git_co_changes.get(p, 0) + 1

    # 2. FalkorDB CO_CHANGES
    graph = db.get_graph("anvaya")
    q = """
    MATCH (f:File {path: $target})-[cc:CO_CHANGES]-(other:File)
    RETURN other.path, cc.count
    """
    res = graph.query(q, {"target": TARGET_FILE})
    graph_co = {r[0]: r[1] for r in res.result_set}

    for path, expected_count in git_co_changes.items():
        assert graph_co.get(path) == expected_count, f"CO_CHANGES mismatch for {path}"


# ---------------------------------------------------------------------------
# REVIEWER INTELLIGENCE & TRUST CONTRACT TESTS (Parts 4, 6, 7)
# ---------------------------------------------------------------------------

def test_reviewers_for_returns_multiple_candidates():
    """Verify reviewers_for returns multiple ranked candidates on a multi-contributor file."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    resp = reviewers_for(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)
    assert resp.total_candidates >= 3
    # Verify ranked by commit_count DESC
    counts = [c.commit_count for c in resp.candidates]
    assert counts == sorted(counts, reverse=True)
    assert resp.candidates[0].person_name == "Tom Christie"
    assert resp.candidates[1].person_name == "George Bogodukhov"


def test_candidates_contain_evidence():
    """Verify every candidate has non-empty evidence commit SHAs and relevant files."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)
    assert resp.refused is False
    assert len(resp.candidates) >= 3

    for c in resp.candidates:
        assert len(c.evidence_commits) > 0
        assert len(c.relevant_files) > 0
        assert TARGET_FILE in c.relevant_files or c.co_change_commits > 0
        assert c.total_relevant_commits == c.direct_commits + c.co_change_commits


def test_no_subjective_language():
    """Verify neither signals nor claims contain subjective or fake confidence words."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)
    forbidden = ["expert", "best", "owner", "master", "perfect", "guaranteed", "flawless", "risky"]

    all_text = " ".join(resp.signals)
    for c in resp.candidates:
        all_text += " " + " ".join(c.signals)

    for word in forbidden:
        assert word not in all_text.lower(), f"Forbidden word '{word}' found in response signals"


def test_unknown_file_refusal_no_path():
    """Verify unknown file produces NO_PATH refusal without guessing."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    resp = impact_reviewers(
        target_file="databases/nonexistent/fake_module.py",
        repo_name=REPO_NAME,
        db_manager=db,
    )
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH
    assert len(resp.candidates) == 0


# ---------------------------------------------------------------------------
# INTEGRATION TESTS: IMPACT-TO-REVIEWERS & MEMORY (Parts 5, 8, 9, 10, 11)
# ---------------------------------------------------------------------------

def test_impact_reviewers_combines_impact_and_reviewer_evidence():
    """
    Key M9 Feature: Verify impact_reviewers combines blast-radius co-changes
    with reviewer routing, returning verified claims and commit evidence.
    """
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)

    assert resp.refused is False
    assert resp.impacted_files_count > 0
    assert len(resp.candidates) >= 3
    assert resp.verification is not None
    assert resp.verification.verified is True
    assert len(resp.claims) > 0
    assert len(resp.evidence) > 0

    top = resp.candidates[0]
    assert top.direct_commits == 35
    assert top.co_change_commits > 0
    assert len(top.relevant_files) > 1


def test_impact_reviewers_is_deterministic():
    """Verify calling impact_reviewers repeatedly produces identical results."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    r1 = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)
    r2 = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)

    assert len(r1.candidates) == len(r2.candidates)
    for c1, c2 in zip(r1.candidates, r2.candidates):
        assert c1.person_name == c2.person_name
        assert c1.direct_commits == c2.direct_commits
        assert c1.co_change_commits == c2.co_change_commits
        assert c1.evidence_commits == c2.evidence_commits


def test_memory_constraint_excludes_reviewer_and_changes_decision():
    """
    GOLDEN MULTI-CONTRIBUTOR SCENARIO (Part 8 & 9):
    Session 1: Tom Christie is selected as #1 candidate reviewer.
    Create active graph constraint: Tom Christie is unavailable for repo:databases.
    Session 2: Fresh query -> Tom Christie is excluded, George Bogodukhov becomes #1!
    """
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # Session 1: Baseline
    s1_resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)
    assert s1_resp.refused is False
    assert s1_resp.candidates[0].person_name == "Tom Christie"
    assert s1_resp.candidates[1].person_name == "George Bogodukhov"

    # Apply M7 Constraint in FalkorDB
    mem_svc = MemoryService(db_manager=db)
    constraint = mem_svc.create_constraint(
        ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject="Tom Christie",
            scope=f"repo:{REPO_NAME}",
        )
    )

    try:
        # Session 2: Changed decision
        s2_resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)
        assert s2_resp.refused is False
        assert s2_resp.candidates[0].person_name == "George Bogodukhov"
        assert len(s2_resp.excluded_candidates) >= 1
        assert s2_resp.excluded_candidates[0].person == "Tom Christie"
        assert len(s2_resp.memory_evidence) >= 1
        assert s2_resp.verification.verified is True
    finally:
        # Clean up constraint
        mem_svc.deactivate_constraint(constraint.id)

    # Session 3: After deactivation, Tom Christie is restored to #1
    s3_resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=db)
    assert s3_resp.candidates[0].person_name == "Tom Christie"


def test_fresh_process_still_respects_constraint():
    """Verify that a brand-new DB manager / fresh process memory respects the constraint."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    mem_svc = MemoryService(db_manager=db)
    constraint = mem_svc.create_constraint(
        ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject="Tom Christie",
            scope=f"repo:{REPO_NAME}",
        )
    )

    try:
        # Create fresh DB manager instance simulating new session/process
        fresh_db = FalkorDBManager()
        fresh_resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=fresh_db)
        assert fresh_resp.candidates[0].person_name == "George Bogodukhov"
        assert any(e.person == "Tom Christie" for e in fresh_resp.excluded_candidates)
    finally:
        mem_svc.deactivate_constraint(constraint.id)


def test_all_candidates_constrained_produces_no_path():
    """When all candidate reviewers are constrained, Anvaya refuses cleanly with NO_PATH."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    mem_svc = MemoryService(db_manager=db)
    # Constrain top candidates on target file
    c1 = mem_svc.create_constraint(
        ConstraintCreate(type=ConstraintType.REVIEWER_UNAVAILABLE, subject="Tom Christie", scope=f"repo:{REPO_NAME}")
    )
    c2 = mem_svc.create_constraint(
        ConstraintCreate(type=ConstraintType.REVIEWER_UNAVAILABLE, subject="George Bogodukhov", scope=f"repo:{REPO_NAME}")
    )
    c3 = mem_svc.create_constraint(
        ConstraintCreate(type=ConstraintType.REVIEWER_UNAVAILABLE, subject="Amin Alaee", scope=f"repo:{REPO_NAME}")
    )
    c4 = mem_svc.create_constraint(
        ConstraintCreate(type=ConstraintType.REVIEWER_UNAVAILABLE, subject="Ricardo Momm", scope=f"repo:{REPO_NAME}")
    )
    c5 = mem_svc.create_constraint(
        ConstraintCreate(type=ConstraintType.REVIEWER_UNAVAILABLE, subject="Vadim Markovtsev", scope=f"repo:{REPO_NAME}")
    )

    try:
        # Request with limit=5 so all 5 candidates are constrained
        resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, limit=5, db_manager=db)
        # Remaining minor contributors or refusal
        if not resp.candidates:
            assert resp.refused is True
            assert resp.reason_code == RefusalCode.NO_PATH
            assert "excluded by active graph constraints" in resp.reason
        else:
            # If any minor candidate exists, none of the constrained ones are included!
            candidate_names = {c.person_name for c in resp.candidates}
            assert "Tom Christie" not in candidate_names
            assert "George Bogodukhov" not in candidate_names
            assert "Amin Alaee" not in candidate_names
    finally:
        for c in [c1, c2, c3, c4, c5]:
            mem_svc.deactivate_constraint(c.id)


def test_kill_switch_when_falkordb_down():
    """When FalkorDB is down, impact_reviewers refuses cleanly with NO_PATH."""
    mock_db = MagicMock()
    mock_db.check_health.return_value = {"connected": False, "error": "Connection refused"}

    resp = impact_reviewers(target_file=TARGET_FILE, repo_name=REPO_NAME, db_manager=mock_db)
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH
    assert "FalkorDB" in resp.reason or "unavailable" in resp.reason.lower()


def test_cypher_injection_safety():
    """Verify malicious Cypher string in target_file is treated literally without injection."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    malicious_target = "databases/backends/postgres.py' OR 1=1 MATCH (n) DETACH DELETE n; //"
    resp = impact_reviewers(target_file=malicious_target, repo_name=REPO_NAME, db_manager=db)
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH

    # Verify repository was not wiped
    graph = db.get_graph("anvaya")
    c_count = graph.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
    assert c_count > 0


# ---------------------------------------------------------------------------
# API ENDPOINT TESTS (Part 12)
# ---------------------------------------------------------------------------

def test_api_impact_reviewers_endpoint_valid_and_refusal():
    """Verify FastAPI GET /api/v1/impact-reviewers endpoint."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # Valid request
    resp = client.get(f"/api/v1/impact-reviewers?file_path={TARGET_FILE}&repo={REPO_NAME}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["refused"] is False
    assert data["target_file"] == TARGET_FILE
    assert len(data["candidates"]) >= 3
    assert data["verification"]["verified"] is True
    assert data["candidates"][0]["person_name"] == "Tom Christie"

    # Also works with alias ?file=
    resp_alias = client.get(f"/api/v1/impact-reviewers?file={TARGET_FILE}&repo={REPO_NAME}")
    assert resp_alias.status_code == 200
    assert resp_alias.json()["refused"] is False

    # Unknown file -> 200 with refused=True and reason_code=NO_PATH
    resp_un = client.get(f"/api/v1/impact-reviewers?file_path=fake/nonexistent.py&repo={REPO_NAME}")
    assert resp_un.status_code == 200
    data_un = resp_un.json()
    assert data_un["refused"] is True
    assert data_un["reason_code"] == "NO_PATH"

    # Missing file parameter -> 400 Bad Request
    resp_bad = client.get("/api/v1/impact-reviewers")
    assert resp_bad.status_code == 400
