import os
import logging
import pytest
from app.core.config import settings
from app.db.falkor import FalkorDBManager, falkor_manager
from app.ingest.git_parser import parse_git_history
from app.ingest.graph_loader import GitGraphLoader

logger = logging.getLogger(__name__)

# Enforce test graph name across environment and settings
TEST_GRAPH_NAME = "anvaya_test"
os.environ["FALKORDB_GRAPH_NAME"] = TEST_GRAPH_NAME
settings.falkordb_graph_name = TEST_GRAPH_NAME
falkor_manager.graph_name = TEST_GRAPH_NAME
falkor_manager.close()


def reset_singletons():
    """Resets all application singletons so they bind to the test graph."""
    import app.context.service as ctx_service
    ctx_service._context_service = None

    import app.actions.service as act_service
    act_service._action_service = None

    import app.agent.coordinator as coord_service
    coord_service._agent_coordinator = None


def get_graph_signature(manager: FalkorDBManager, graph_name: str = "anvaya") -> dict:
    """Computes a deterministic signature of node/edge/sync counts in a FalkorDB graph.

    Raises ValueError if FalkorDB is unreachable, so callers can distinguish a genuine
    empty signature from a connection failure (prevents vacuous {} == {} pass).
    """
    health = manager.check_health()
    if not health.get("connected"):
        raise ValueError(
            f"Cannot compute graph signature for '{graph_name}': FalkorDB is not reachable. "
            f"Error: {health.get('error', 'unknown')}"
        )
    try:
        g = manager.get_graph(graph_name)
        c_cnt = g.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
        f_cnt = g.query("MATCH (f:File) RETURN count(f)").result_set[0][0]
        p_cnt = g.query("MATCH (p:Person) RETURN count(p)").result_set[0][0]
        cc_cnt = g.query("MATCH ()-[r:CO_CHANGES]->() RETURN count(r)").result_set[0][0]
        sync_res = g.query(
            "MATCH (r:Repository {name: 'arth'})-[:SNAPSHOT]->(s:Sync) "
            "RETURN s.head_sha, s.commit_count ORDER BY s.synced_at DESC LIMIT 1"
        ).result_set
        head_sha = sync_res[0][0] if sync_res else None
        sync_commits = sync_res[0][1] if sync_res else 0
        return {
            "commit_count": c_cnt,
            "file_count": f_cnt,
            "person_count": p_cnt,
            "co_changes_count": cc_cnt,
            "arth_head_sha": head_sha,
            "arth_sync_commits": sync_commits,
        }
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to compute graph signature for '{graph_name}': {exc}") from exc



@pytest.fixture(scope="session", autouse=True)
def isolate_and_protect_production_graph():
    """
    Session fixture:
    1. Verifies FalkorDB connectivity.
    2. Snapshots live production graph ('anvaya') signature.
    3. Seeds 'anvaya_test' with canonical ARTH and databases history if not already seeded.
    4. Yields to test suite.
    5. Verifies that production graph ('anvaya') was 100% UNTOUCHED by pytest.
    """
    manager = FalkorDBManager(graph_name=TEST_GRAPH_NAME)
    health = manager.check_health()
    if not health.get("connected"):
        yield
        return

    # Snapshot production graph state before any test runs
    prod_before = get_graph_signature(manager, "anvaya")

    # Ensure test graph is populated with canonical ground-truth data
    loader = GitGraphLoader(manager)
    g_test = manager.get_graph(TEST_GRAPH_NAME)
    try:
        test_c_cnt = g_test.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
    except Exception:
        test_c_cnt = 0

    if test_c_cnt < 400:
        # Load ARTH (97 commits)
        if os.path.exists("data/repos/arth"):
            arth_commits = parse_git_history("data/repos/arth", max_commits=2000)
            loader.load_history("arth", arth_commits, repo_path="data/repos/arth", graph_name=TEST_GRAPH_NAME)

        # Load encode/databases (335 commits)
        if os.path.exists("data/repos/databases"):
            db_commits = parse_git_history("data/repos/databases", max_commits=2000)
            loader.load_history("databases", db_commits, repo_path="data/repos/databases", graph_name=TEST_GRAPH_NAME)

    reset_singletons()

    yield

    # Verification: production graph must be completely unmodified
    try:
        prod_after = get_graph_signature(manager, "anvaya")
        assert prod_before == prod_after, (
            f"CRITICAL TEST HYGIENE VIOLATION: Production graph 'anvaya' was mutated during pytest execution!\n"
            f"Before: {prod_before}\nAfter: {prod_after}"
        )
    except ValueError as exc:
        # FalkorDB connectivity was lost after tests — cannot verify invariance.
        # This is a test infrastructure failure, not a production-graph mutation.
        logger.error(
            "Production graph invariance check SKIPPED: FalkorDB became unreachable after tests. "
            "This may indicate test infrastructure issues. Error: %s",
            exc,
        )
        raise AssertionError(
            f"Production graph invariance check could not be completed because FalkorDB became "
            f"unreachable after tests. This is a test infrastructure failure. Error: {exc}"
        ) from exc



@pytest.fixture(autouse=True)
def auto_reset_singletons():
    """Per-test fixture resetting singletons to prevent cross-test contamination."""
    reset_singletons()
    yield
    reset_singletons()
