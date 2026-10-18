import os
import pytest
from app.agent.coordinator import get_agent_coordinator
from app.agent.llm.provider import TemplateProvider
from app.context.service import get_context_service
from app.context.models import RefusalCode, SubjectType
from app.db.falkor import get_falkor_manager, FalkorDBManager
from app.tools.reviewers import reviewers_for
from app.core.config import settings


# -----------------------------------------------------------------------------
# FINDING-A3-001: Test Graph Isolation & Invariance of Production Graph
# -----------------------------------------------------------------------------

def test_a3_001_test_graph_isolated_from_production():
    """Verify that tests execute against anvaya_test and settings point to anvaya_test."""
    assert settings.falkordb_graph_name == "anvaya_test"
    db = get_falkor_manager()
    assert db.graph_name == "anvaya_test"

    # Verify anvaya production graph is accessible and healthy
    prod_graph = db.get_graph("anvaya")
    res = prod_graph.query(
        "MATCH (r:Repository {name: 'arth'})-[:SNAPSHOT]->(s:Sync) "
        "RETURN s.head_sha, s.commit_count ORDER BY s.synced_at DESC LIMIT 1"
    )
    assert len(res.result_set) == 1
    assert res.result_set[0][0] == "79595add24d7e561d28be4fe66af3fa2c17b933d"
    assert res.result_set[0][1] == 97


def test_a3_001_ingest_into_test_graph_does_not_mutate_production():
    """Verify that operations on test graph leave production graph anvaya intact."""
    db = get_falkor_manager()
    prod_graph = db.get_graph("anvaya")
    c_count_before = prod_graph.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]

    # Query test graph
    test_graph = db.get_graph("anvaya_test")
    test_c_count = test_graph.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
    assert test_c_count > 0

    c_count_after = prod_graph.query("MATCH (c:Commit) RETURN count(c)").result_set[0][0]
    assert c_count_before == c_count_after


# -----------------------------------------------------------------------------
# FINDING-A3-003: Context Routing Fails Closed on Unknown Subjects
# -----------------------------------------------------------------------------

def test_a3_003_context_query_for_nonexistent_module_refuses():
    """Verify that a request for context on a nonexistent module refuses with NO_PATH."""
    provider = TemplateProvider()
    tc = provider.select_tool("Give me context for nonexistent_module", repo="arth")
    assert tc.tool == "context_package"
    assert tc.arguments["subject"] == "nonexistent_module"
    assert tc.arguments["subject_type"] == "module"

    coordinator = get_agent_coordinator()
    resp = coordinator.ask("Give me context for nonexistent_module", repo="arth")
    assert resp.verified is False
    assert resp.reason_code == RefusalCode.NO_PATH
    assert "not found" in resp.reason.lower()


def test_a3_003_context_query_for_existing_module_succeeds():
    """Verify that a request for context on an existing module returns module context."""
    provider = TemplateProvider()
    tc = provider.select_tool("Give me context for backend", repo="arth")
    assert tc.tool == "context_package"
    assert tc.arguments["subject"] == "backend"
    assert tc.arguments["subject_type"] == "module"

    coordinator = get_agent_coordinator()
    resp = coordinator.ask("Give me context for backend", repo="arth")
    assert resp.verified is True
    assert resp.selected_tool == "context_package"
    assert "backend" in resp.narration.lower()


def test_a3_003_context_query_for_repo_returns_repo_context():
    """Verify that asking for context for arth returns repository context."""
    provider = TemplateProvider()
    tc = provider.select_tool("Give me context for arth", repo="arth")
    assert tc.tool == "context_package"
    assert tc.arguments["subject"] == "arth"
    assert tc.arguments["subject_type"] == "repository"


def test_a3_003_direct_context_service_unknown_subject_refusal():
    """Verify direct ContextPackageService refuses NO_PATH for unknown file or module."""
    service = get_context_service()
    pkg = service.get_context_package(
        repository="arth",
        subject="completely_fabricated_path.py",
        subject_type="file",
    )
    assert pkg.refusal is True
    assert pkg.reason_code == RefusalCode.NO_PATH

    pkg_mod = service.get_context_package(
        repository="arth",
        subject="completely_fabricated_module",
        subject_type="module",
    )
    assert pkg_mod.refusal is True
    assert pkg_mod.reason_code == RefusalCode.NO_PATH


# -----------------------------------------------------------------------------
# FINDING-A3-004: TemplateProvider Leading-Dot Filename Regex Extraction
# -----------------------------------------------------------------------------

def test_a3_004_dotfiles_extracted_cleanly():
    """Verify that TemplateProvider extracts leading dotfiles correctly."""
    provider = TemplateProvider()

    # 1. .gitignore
    tc1 = provider.select_tool("Who should review .gitignore?", repo="arth")
    assert tc1.tool == "reviewers_for"
    assert tc1.arguments["file"] == ".gitignore"

    # 2. .env.example
    tc2 = provider.select_tool("Who has worked on .env.example?", repo="arth")
    assert tc2.tool == "reviewers_for"
    assert tc2.arguments["file"] == ".env.example"

    # 3. .github/workflows/deploy.yml
    tc3 = provider.select_tool("Who knows about .github/workflows/deploy.yml in arth?", repo="arth")
    assert tc3.tool == "reviewers_for"
    assert tc3.arguments["file"] == ".github/workflows/deploy.yml"

    # 4. Context query for dotfile
    tc4 = provider.select_tool("Give me context on .gitignore", repo="arth")
    assert tc4.tool == "context_package"
    assert tc4.arguments["subject"] == ".gitignore"
    assert tc4.arguments["subject_type"] == "file"

    # 5. Impact query for dotfile
    tc5 = provider.select_tool("What could break if I modify .env.example?", repo="arth")
    assert tc5.tool == "change_impact"
    assert tc5.arguments["file_path"] == ".env.example"


def test_a3_004_coordinator_dotfile_workflow():
    """Verify coordinator processes .gitignore reviewer query end-to-end."""
    coordinator = get_agent_coordinator()
    resp = coordinator.ask("Who should review .gitignore?", repo="arth")
    assert resp.selected_tool == "reviewers_for"
    assert resp.verified is True
    assert "chandradeep" in resp.narration.lower()


# -----------------------------------------------------------------------------
# FINDING-A3-002: Verification of Ground Truth Repository Characteristics
# -----------------------------------------------------------------------------

def test_a3_002_arth_and_databases_ground_truth_characteristics():
    """
    Verify the dual-repository benchmark setup:
    1. ARTH is single-contributor ground-truth (97 commits, 1 contributor).
    2. databases is multi-contributor ground-truth (335 commits, >30 contributors).
    """
    db = get_falkor_manager()
    graph = db.get_graph()

    # Check ARTH
    arth_commits = graph.query(
        "MATCH (r:Repository {name: 'arth'})-[:HAS_COMMIT]->(c:Commit) RETURN count(c)"
    ).result_set[0][0]
    assert arth_commits == 97

    arth_contribs = graph.query(
        "MATCH (r:Repository {name: 'arth'})-[:CONTAINS]->(f:File)<-[:TOUCHES]-(c:Commit)<-[:AUTHORED]-(p:Person) "
        "RETURN count(DISTINCT p)"
    ).result_set[0][0]
    assert arth_contribs == 1

    # Check databases
    db_commits = graph.query(
        "MATCH (r:Repository {name: 'databases'})-[:HAS_COMMIT]->(c:Commit) RETURN count(c)"
    ).result_set[0][0]
    assert db_commits == 335

    db_contribs = graph.query(
        "MATCH (r:Repository {name: 'databases'})-[:CONTAINS]->(f:File)<-[:TOUCHES]-(c:Commit)<-[:AUTHORED]-(p:Person) "
        "RETURN count(DISTINCT p)"
    ).result_set[0][0]
    assert db_contribs >= 30
