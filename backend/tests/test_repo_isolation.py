import pytest
from app.db.falkor import FalkorDBManager
from app.tools.reviewers import reviewers_for, get_verified_reviewers
from app.tools.impact import change_impact
from app.context.service import get_context_service
from app.api.routes.graph import get_evidence_path, get_graph_data
from app.memory.models import ConstraintCreate, ConstraintType
from app.memory.service import MemoryService


@pytest.fixture(scope="module")
def db_manager():
    mgr = FalkorDBManager()
    health = mgr.check_health()
    if not health.get("connected"):
        pytest.skip("FalkorDB is offline; skipping cross-repository isolation tests.")
    return mgr


def test_readme_collision_isolation_reviewers(db_manager):
    """
    FINDING-001 Verification:
    Both 'arth' and 'databases' have a 'README.md' file.
    Ensure reviewers_for returns ONLY the respective repository's contributors.
    """
    res_arth = reviewers_for("README.md", repo_name="arth", db_manager=db_manager)
    assert res_arth.total_candidates > 0
    arth_cand_names = [c.person_name for c in res_arth.candidates]
    assert "chandradeep05" in arth_cand_names
    assert "Tom Christie" not in arth_cand_names
    assert "Amin Alaee" not in arth_cand_names

    res_db = reviewers_for("README.md", repo_name="databases", db_manager=db_manager)
    assert res_db.total_candidates > 0
    db_cand_names = [c.person_name for c in res_db.candidates]
    assert "Tom Christie" in db_cand_names
    assert "chandradeep05" not in db_cand_names


def test_readme_collision_isolation_verified_reviewers(db_manager):
    """
    Ensure get_verified_reviewers builds evidence and claims strictly
    isolated to the target repository when target file names collide.
    """
    v_arth = get_verified_reviewers(target_file="README.md", repo_name="arth", db_manager=db_manager)
    assert not v_arth.refused
    assert v_arth.candidates[0].person_name == "chandradeep05"
    assert all("Tom Christie" not in ev.properties.get("author", "") for ev in v_arth.evidence)

    v_db = get_verified_reviewers(target_file="README.md", repo_name="databases", db_manager=db_manager)
    assert not v_db.refused
    assert v_db.candidates[0].person_name == "Tom Christie"
    assert all("chandradeep05" not in ev.properties.get("author", "") for ev in v_db.evidence)


def test_readme_collision_isolation_change_impact(db_manager):
    """
    Verify blast-radius and change_impact isolation:
    Co-changing files and related people must not leak across repositories.
    """
    impact_arth = change_impact("README.md", repo_name="arth", db_manager=db_manager)
    assert not impact_arth.refused
    arth_impacted = [f.path for f in impact_arth.impacted_files]
    arth_people = [p.person for p in impact_arth.related_people]

    # Arth impacted files are in backend/frontend
    assert any(p.startswith("backend/") or p.startswith("frontend/") for p in arth_impacted)
    assert not any(p.startswith("databases/") for p in arth_impacted)
    assert "chandradeep05" in arth_people
    assert "Tom Christie" not in arth_people

    impact_db = change_impact("README.md", repo_name="databases", db_manager=db_manager)
    assert not impact_db.refused
    db_impacted = [f.path for f in impact_db.impacted_files]
    db_people = [p.person for p in impact_db.related_people]

    # Databases impacted files are in databases/ or root
    assert any("databases" in p or p.endswith(".py") for p in db_impacted)
    assert not any(p.startswith("backend/") or p.startswith("frontend/") for p in db_impacted)
    assert "Tom Christie" in db_people
    assert "chandradeep05" not in db_people


def test_readme_collision_isolation_context_package(db_manager):
    """
    Verify Context Package service respects repository isolation on identical file paths.
    """
    ctx_svc = get_context_service()

    pkg_arth = ctx_svc.get_context_package(
        repository="arth",
        subject="README.md",
        subject_type="file",
    )
    assert not pkg_arth.refusal
    arth_contrib_names = [c.name for c in pkg_arth.contributors]
    assert "chandradeep05" in arth_contrib_names
    assert "Tom Christie" not in arth_contrib_names

    pkg_db = ctx_svc.get_context_package(
        repository="databases",
        subject="README.md",
        subject_type="file",
    )
    assert not pkg_db.refusal
    db_contrib_names = [c.name for c in pkg_db.contributors]
    assert "Tom Christie" in db_contrib_names
    assert "chandradeep05" not in db_contrib_names


def test_graph_evidence_path_repo_scoping(db_manager):
    """
    Verify get_evidence_path checks repository boundary:
    An author path only exists if the person authored commits in THAT specific repository.
    """
    # Tom Christie in databases README.md -> True
    p1 = get_evidence_path(
        target_file="README.md",
        reviewer_name="Tom Christie",
        repo="databases",
        db_manager=db_manager,
    )
    assert p1.path_found is True
    assert p1.path_type == "direct"

    # Tom Christie in arth README.md -> False (zero commits authored in arth)
    p2 = get_evidence_path(
        target_file="README.md",
        reviewer_name="Tom Christie",
        repo="arth",
        db_manager=db_manager,
    )
    assert p2.path_found is False

    # chandradeep05 in arth README.md -> True
    p3 = get_evidence_path(
        target_file="README.md",
        reviewer_name="chandradeep05",
        repo="arth",
        db_manager=db_manager,
    )
    assert p3.path_found is True

    # chandradeep05 in databases README.md -> False (zero commits authored in databases)
    p4 = get_evidence_path(
        target_file="README.md",
        reviewer_name="chandradeep05",
        repo="databases",
        db_manager=db_manager,
    )
    assert p4.path_found is False


def test_graph_overview_repo_scoping(db_manager):
    """
    Verify get_graph_data repository-level overview isolates nodes and edges.
    """
    g_arth = get_graph_data(repo="arth", max_nodes=50, max_edges=100, db_manager=db_manager)
    for node in g_arth.nodes:
        if node.type == "File":
            assert not node.properties.get("path", "").startswith("databases/")
        elif node.type == "Person":
            assert node.properties.get("name") != "Tom Christie"

    g_db = get_graph_data(repo="databases", max_nodes=50, max_edges=100, db_manager=db_manager)
    for node in g_db.nodes:
        if node.type == "File":
            assert not node.properties.get("path", "").startswith("backend/")
            assert not node.properties.get("path", "").startswith("frontend/")
        elif node.type == "Person":
            assert node.properties.get("name") != "chandradeep05"


def test_repository_memory_constraint_isolation(db_manager):
    """
    Verify persistent memory constraints scoped to one repository
    do not leak into or affect recommendations in another repository.
    """
    service = MemoryService(db_manager=db_manager)
    test_subj = "Test Isolated Person"

    # Record a constraint scoped strictly to databases
    c1 = service.create_constraint(
        ConstraintCreate(
            type=ConstraintType.REVIEWER_UNAVAILABLE,
            subject=test_subj,
            scope="repo:databases",
        )
    )

    try:
        # Check active constraints in databases
        db_constraints = service.get_active_constraints(
            type=ConstraintType.REVIEWER_UNAVAILABLE.value,
            scope="repo:databases",
        )
        assert any(c.subject == test_subj for c in db_constraints)

        # Check active constraints in arth -> must NOT contain test_subj
        arth_constraints = service.get_active_constraints(
            type=ConstraintType.REVIEWER_UNAVAILABLE.value,
            scope="repo:arth",
        )
        assert not any(c.subject == test_subj for c in arth_constraints)
    finally:
        service.deactivate_constraint(c1.id)
