import pytest
import os
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager, get_falkor_manager
from app.ingest.models import GitCommit, GitTouch
from app.ingest.graph_loader import GitGraphLoader, generate_co_change_pairs
from app.ingest.git_parser import parse_git_history
from app.tools.impact import change_impact
from app.tools.models import ChangeImpactResponse, ImpactedFile
from app.tools.registry import tool_registry, InvalidToolArgumentsError
from app.evidence.models import RefusalCode

client = TestClient(app)


# ---------------------------------------------------------------------------
# UNIT TESTS
# ---------------------------------------------------------------------------

def test_co_change_pair_generation():
    """Unit test 1: Basic pair generation from a commit with touches."""
    commit = GitCommit(
        sha="c1",
        author_name="Alice",
        author_email="alice@example.com",
        timestamp="2026-10-01T12:00:00Z",
        subject="test commit",
        touches=[
            GitTouch(path="backend/a.py"),
            GitTouch(path="backend/b.py"),
            GitTouch(path="backend/c.py"),
        ],
    )
    pairs = generate_co_change_pairs([commit])
    # 3 unique files -> 3 unordered pairs
    assert len(pairs) == 3
    pair_files = [(p["f1"], p["f2"]) for p in pairs]
    assert ("backend/a.py", "backend/b.py") in pair_files
    assert ("backend/a.py", "backend/c.py") in pair_files
    assert ("backend/b.py", "backend/c.py") in pair_files


def test_co_change_self_pairs_never_generated():
    """Unit test 2: Self-pairs are never generated even if touches repeat path."""
    commit = GitCommit(
        sha="c1",
        author_name="Alice",
        author_email="alice@example.com",
        timestamp="2026-10-01T12:00:00Z",
        subject="test commit",
        touches=[
            GitTouch(path="backend/a.py"),
            GitTouch(path="backend/a.py"),
            GitTouch(path="backend/b.py"),
        ],
    )
    pairs = generate_co_change_pairs([commit])
    assert len(pairs) == 1
    assert pairs[0]["f1"] == "backend/a.py"
    assert pairs[0]["f2"] == "backend/b.py"
    for p in pairs:
        assert p["f1"] != p["f2"]


def test_co_change_pair_ordering_canonical():
    """Unit test 3: Pair ordering is strictly lexical canonical (f1 < f2)."""
    commit = GitCommit(
        sha="c1",
        author_name="Alice",
        author_email="alice@example.com",
        timestamp="2026-10-01T12:00:00Z",
        subject="test commit",
        touches=[
            GitTouch(path="zebra.py"),
            GitTouch(path="apple.py"),
        ],
    )
    pairs = generate_co_change_pairs([commit])
    assert len(pairs) == 1
    assert pairs[0]["f1"] == "apple.py"
    assert pairs[0]["f2"] == "zebra.py"
    assert pairs[0]["f1"] < pairs[0]["f2"]


def test_co_change_counts_increment_correctly():
    """Unit test 4: Counts increment across multiple commits touching the pair."""
    c1 = GitCommit(
        sha="c1",
        author_name="Alice",
        author_email="alice@example.com",
        timestamp="2026-10-01T10:00:00Z",
        subject="first",
        touches=[GitTouch(path="a.py"), GitTouch(path="b.py")],
    )
    c2 = GitCommit(
        sha="c2",
        author_name="Bob",
        author_email="bob@example.com",
        timestamp="2026-10-02T10:00:00Z",
        subject="second",
        touches=[GitTouch(path="a.py"), GitTouch(path="b.py"), GitTouch(path="c.py")],
    )
    pairs = generate_co_change_pairs([c1, c2])
    ab = next(p for p in pairs if p["f1"] == "a.py" and p["f2"] == "b.py")
    assert ab["count"] == 2


def test_co_change_last_ts_updates_correctly():
    """Unit test 5: last_ts updates to the most recent commit timestamp."""
    c1 = GitCommit(
        sha="c1",
        author_name="Alice",
        author_email="alice@example.com",
        timestamp="2026-10-01T10:00:00Z",
        subject="first",
        touches=[GitTouch(path="a.py"), GitTouch(path="b.py")],
    )
    c2 = GitCommit(
        sha="c2",
        author_name="Bob",
        author_email="bob@example.com",
        timestamp="2026-10-05T14:30:00Z",
        subject="second",
        touches=[GitTouch(path="a.py"), GitTouch(path="b.py")],
    )
    pairs = generate_co_change_pairs([c1, c2])
    ab = next(p for p in pairs if p["f1"] == "a.py" and p["f2"] == "b.py")
    assert ab["last_ts"] == "2026-10-05T14:30:00Z"


def test_change_impact_unknown_file_refusal_no_path():
    """Unit test 6: Unknown file produces NO_PATH refusal without guessing."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    resp = change_impact(
        target_file="completely/nonexistent/file.py",
        repo_name="arth",
        db_manager=db,
    )
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH
    assert "not found" in resp.reason.lower()
    assert len(resp.impacted_files) == 0


def test_change_impact_known_file_no_co_changes():
    """Unit test 7: Known file with no co-change evidence returns valid result with empty impacted_files."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # Setup isolated test graph with a single isolated file touched by 1 commit
    graph_name = "test_no_co_changes_graph"
    client_f = db.get_client()
    try:
        client_f.select_graph(graph_name).delete()
    except Exception:
        pass

    graph = client_f.select_graph(graph_name)
    loader = GitGraphLoader(db)
    loader.ensure_schema(graph)

    single_commit = GitCommit(
        sha="solo111",
        author_name="Alice",
        author_email="alice@test.com",
        timestamp="2026-10-01T12:00:00Z",
        subject="solo commit",
        touches=[GitTouch(path="isolated.py")],
    )
    loader.load_history("test_repo", [single_commit], graph_name=graph_name)

    resp = change_impact(
        target_file="isolated.py",
        repo_name="test_repo",
        graph_name=graph_name,
        db_manager=db,
    )
    assert resp.refused is False
    assert len(resp.impacted_files) == 0
    assert any("no historical co-change" in s.lower() for s in resp.signals)

    # Cleanup
    try:
        client_f.select_graph(graph_name).delete()
    except Exception:
        pass


def test_change_impact_evidence_references_resolve():
    """Unit test 8: All claims reference evidence IDs that exist in provided evidence."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    resp = change_impact(
        target_file="backend/app/engines/assistant/engine.py",
        repo_name="arth",
        db_manager=db,
    )
    if resp.refused:
        pytest.skip("File not yet in graph")

    assert len(resp.claims) > 0
    assert len(resp.evidence) > 0
    ev_ids = {e.id for e in resp.evidence}
    for claim in resp.claims:
        assert len(claim.evidence_ids) > 0
        for eid in claim.evidence_ids:
            assert eid in ev_ids


def test_change_impact_invalid_tool_arguments_rejected():
    """Unit test 9: Invalid arguments to change_impact tool are strictly rejected."""
    with pytest.raises(InvalidToolArgumentsError):
        tool_registry.execute_tool("change_impact", {})

    with pytest.raises(InvalidToolArgumentsError):
        tool_registry.execute_tool("change_impact", {"file_path": ""})


def test_change_impact_arbitrary_cypher_injection_safety():
    """Unit test 10: Cypher injection payloads are treated as literal file paths; graph remains safe."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    malicious_target = "backend/app/main.py' OR 1=1 MATCH (n) DETACH DELETE n; //"
    resp = change_impact(
        target_file=malicious_target,
        repo_name="arth",
        db_manager=db,
    )
    # Refuses cleanly as unknown file without executing malicious Cypher
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH

    # Verify repository was not wiped
    graph = db.get_graph("anvaya")
    c_count = graph.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
    assert c_count > 0


# ---------------------------------------------------------------------------
# INTEGRATION TESTS: Synthetic Repository
# ---------------------------------------------------------------------------

def test_synthetic_repo_co_changes_and_blast_radius():
    """
    Integration test on deterministic synthetic repository:
    commit A: a.py, b.py
    commit B: a.py, b.py, c.py
    commit C: a.py, c.py

    Expected relationships in FalkorDB:
    a.py <-> b.py = 2
    a.py <-> c.py = 2
    b.py <-> c.py = 1
    """
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    graph_name = "test_synthetic_m8_graph"
    client_f = db.get_client()
    try:
        client_f.select_graph(graph_name).delete()
    except Exception:
        pass

    graph = client_f.select_graph(graph_name)
    loader = GitGraphLoader(db)
    loader.ensure_schema(graph)

    commits = [
        GitCommit(
            sha="sha_A",
            author_name="Alice",
            author_email="alice@example.com",
            timestamp="2026-10-01T10:00:00Z",
            subject="commit A",
            touches=[GitTouch(path="a.py"), GitTouch(path="b.py")],
        ),
        GitCommit(
            sha="sha_B",
            author_name="Bob",
            author_email="bob@example.com",
            timestamp="2026-10-02T10:00:00Z",
            subject="commit B",
            touches=[GitTouch(path="a.py"), GitTouch(path="b.py"), GitTouch(path="c.py")],
        ),
        GitCommit(
            sha="sha_C",
            author_name="Charlie",
            author_email="charlie@example.com",
            timestamp="2026-10-03T10:00:00Z",
            subject="commit C",
            touches=[GitTouch(path="a.py"), GitTouch(path="c.py")],
        ),
    ]

    summary = loader.load_history("synth_repo", commits, graph_name=graph_name)
    assert summary.commit_count == 3
    assert summary.file_count == 3
    assert summary.co_changes_count == 3

    # Direct Cypher verification of CO_CHANGES counts
    q = """
    MATCH (f1:File)-[cc:CO_CHANGES]->(f2:File)
    RETURN f1.path, f2.path, cc.count, cc.last_ts
    ORDER BY f1.path, f2.path
    """
    res = graph.query(q).result_set
    assert len(res) == 3
    # Pair a.py -> b.py count = 2
    assert res[0][0] == "a.py" and res[0][1] == "b.py" and res[0][2] == 2
    # Pair a.py -> c.py count = 2
    assert res[1][0] == "a.py" and res[1][1] == "c.py" and res[1][2] == 2
    # Pair b.py -> c.py count = 1
    assert res[2][0] == "b.py" and res[2][1] == "c.py" and res[2][2] == 1

    # Execute change_impact on a.py
    resp_a = change_impact("a.py", repo_name="synth_repo", graph_name=graph_name, db_manager=db)
    assert resp_a.refused is False
    assert len(resp_a.impacted_files) == 2
    paths = {f.path: f.co_change_count for f in resp_a.impacted_files}
    assert paths["b.py"] == 2
    assert paths["c.py"] == 2
    assert resp_a.verification.verified is True

    # Check evidence SHAs
    b_file = next(f for f in resp_a.impacted_files if f.path == "b.py")
    assert "sha_A" in b_file.evidence_shas
    assert "sha_B" in b_file.evidence_shas

    c_file = next(f for f in resp_a.impacted_files if f.path == "c.py")
    assert "sha_B" in c_file.evidence_shas
    assert "sha_C" in c_file.evidence_shas

    # Test unknown file in this graph
    resp_unknown = change_impact("unknown.py", repo_name="synth_repo", graph_name=graph_name, db_manager=db)
    assert resp_unknown.refused is True
    assert resp_unknown.reason_code == RefusalCode.NO_PATH

    # Cleanup
    try:
        client_f.select_graph(graph_name).delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# REAL ARTH REPOSITORY VERIFICATION & RAW GIT CROSS-CHECK
# ---------------------------------------------------------------------------

def test_real_arth_change_impact_verified():
    """Verify real ARTH change_impact on high-frequency co-changing file."""
    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    target = "backend/app/engines/assistant/engine.py"
    resp = change_impact(target_file=target, repo_name="arth", db_manager=db)

    assert resp.refused is False
    assert resp.target_file == target
    assert len(resp.impacted_files) > 0
    assert resp.verification is not None
    assert resp.verification.verified is True
    assert len(resp.claims) > 0
    assert len(resp.evidence) > 0

    # Top impacted file should be research engine with count 9
    top = resp.impacted_files[0]
    assert top.path == "backend/app/engines/research/engine.py"
    assert top.co_change_count == 9
    assert len(top.evidence_shas) == 9


def test_real_arth_raw_git_ground_truth_cross_check():
    """
    Rule 8 requirement: Independently calculate co-changes for a file
    from raw git history and assert 100% equivalence with FalkorDB graph results.
    """
    repo_path = "data/repos/arth"
    if not os.path.exists(repo_path):
        pytest.skip(f"Repository not found at {repo_path}")

    db = FalkorDBManager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    target = "backend/app/engines/assistant/engine.py"

    # 1. Independent Ground Truth calculation from GitCommits directly
    all_commits = parse_git_history(repo_path, max_commits=2000)
    git_co_changes = {}
    git_shared_shas = {}

    for c in all_commits:
        touched = {t.path.replace("\\", "/").strip("/") for t in c.touches if t.path}
        if target in touched:
            for other in touched:
                if other != target:
                    git_co_changes[other] = git_co_changes.get(other, 0) + 1
                    if other not in git_shared_shas:
                        git_shared_shas[other] = []
                    git_shared_shas[other].append(c.sha)

    # 2. FalkorDB graph query via change_impact
    graph_resp = change_impact(target_file=target, repo_name="arth", limit=10, db_manager=db)

    # 3. Assert graph results exactly equal git ground truth for returned files
    assert len(graph_resp.impacted_files) > 0
    for imp in graph_resp.impacted_files:
        assert imp.path in git_co_changes, f"Graph returned file {imp.path} not in Git history"
        assert imp.co_change_count == git_co_changes[imp.path], (
            f"Count mismatch for {imp.path}: graph={imp.co_change_count}, git={git_co_changes[imp.path]}"
        )
        assert set(imp.evidence_shas) == set(git_shared_shas[imp.path]), (
            f"Evidence SHA mismatch for {imp.path}"
        )


# ---------------------------------------------------------------------------
# KILL SWITCH & API ENDPOINT TESTS
# ---------------------------------------------------------------------------

def test_change_impact_kill_switch_when_falkordb_down():
    """When FalkorDB is disconnected, /impact and change_impact refuse cleanly without local cache."""
    mock_db = MagicMock()
    mock_db.check_health.return_value = {"connected": False, "error": "Connection refused"}

    resp = change_impact(
        target_file="backend/app/engines/assistant/engine.py",
        repo_name="arth",
        db_manager=mock_db,
    )
    assert resp.refused is True
    assert resp.reason_code == RefusalCode.NO_PATH
    assert "FalkorDB" in resp.reason or "unavailable" in resp.reason.lower()


def test_api_impact_endpoint_valid_and_refusal():
    """Verify FastAPI /api/v1/impact endpoints: valid query, unknown file, and validation errors."""
    db = get_falkor_manager()
    if not db.check_health()["connected"]:
        pytest.skip("FalkorDB is not connected")

    # Valid query
    resp = client.get("/api/v1/impact?file_path=backend/app/engines/assistant/engine.py")
    assert resp.status_code == 200
    data = resp.json()
    assert data["refused"] is False
    assert data["target_file"] == "backend/app/engines/assistant/engine.py"
    assert len(data["impacted_files"]) > 0
    assert data["verification"]["verified"] is True

    # Also works with alias ?file=
    resp_alias = client.get("/api/v1/impact?file=backend/app/engines/assistant/engine.py")
    assert resp_alias.status_code == 200
    assert resp_alias.json()["refused"] is False

    # Unknown file query -> 200 with refused=True and reason_code=NO_PATH
    resp_unknown = client.get("/api/v1/impact?file_path=completely/fake/file.py")
    assert resp_unknown.status_code == 200
    data_unknown = resp_unknown.json()
    assert data_unknown["refused"] is True
    assert data_unknown["reason_code"] == "NO_PATH"

    # Missing file parameter -> 400 Bad Request
    resp_bad = client.get("/api/v1/impact")
    assert resp_bad.status_code == 400
