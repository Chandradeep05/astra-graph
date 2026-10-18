"""
Deployment and diagnostic tests for FalkorDB, CORS, and health monitoring.
Validates:
- Diagnostic error classification for all failure modes without secret leakage.
- Health endpoint structured response (200 on healthy, 503 on unreachable).
- CORS headers for cross-origin requests.
- Fail-closed memory constraint handling in impact_reviewers.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager, classify_falkordb_error
from app.tools.impact_reviewers import impact_reviewers
from app.evidence.models import RefusalCode


client = TestClient(app)


class TestFalkorDBDiagnostics:
    """Test safe diagnostic categorization without secret leakage."""

    def test_dns_resolution_failure(self):
        exc = RuntimeError("Error connecting to host: [Errno -2] Name or service not known (getaddrinfo failed)")
        res = classify_falkordb_error(exc)
        assert res["error_category"] == "dns_resolution_failed"
        assert "password" not in res["detail"].lower()
        assert "FALKORDB_HOST" in res["detail"]

    def test_tcp_connection_refused(self):
        exc = ConnectionRefusedError("[WinError 10061] No connection could be made because the target machine actively refused it")
        res = classify_falkordb_error(exc)
        assert res["error_category"] == "tcp_connection_refused"
        assert "running" in res["detail"].lower() or "refused" in res["detail"].lower()

    def test_authentication_failure_no_secret_leak(self):
        exc = Exception("WRONGPASS invalid password 'secret_pass_12345' supplied")
        res = classify_falkordb_error(exc)
        assert res["error_category"] == "authentication_failed"
        assert "secret_pass_12345" not in res["detail"]
        assert "FALKORDB_PASSWORD" in res["detail"]

    def test_tls_ssl_handshake_failure(self):
        exc = Exception("SSL: CERTIFICATE_VERIFY_FAILED certificate verify failed: self-signed certificate")
        res = classify_falkordb_error(exc)
        assert res["error_category"] == "tls_ssl_handshake_failed"
        assert "FALKORDB_SSL" in res["detail"]

    def test_connection_timeout(self):
        exc = TimeoutError("Connection to Redis server timed out after 5000ms")
        res = classify_falkordb_error(exc)
        assert res["error_category"] == "connection_timeout"

    def test_graph_not_found(self):
        exc = Exception("Graph does not exist: anvaya")
        res = classify_falkordb_error(exc)
        assert res["error_category"] == "graph_not_found"
        assert "ingestion" in res["detail"].lower()

    def test_protocol_incompatibility(self):
        exc = Exception("unknown command 'GRAPH.QUERY'")
        res = classify_falkordb_error(exc)
        assert res["error_category"] == "protocol_incompatible"

    def test_generic_failure(self):
        exc = ValueError("Unexpected internal state")
        res = classify_falkordb_error(exc)
        assert res["error_category"] == "database_unavailable"


class TestHealthEndpointDeployment:
    """Test health endpoint behavior under cloud deployment conditions."""

    def test_health_503_when_falkordb_disconnected(self):
        with patch.object(FalkorDBManager, "check_health") as mock_h:
            mock_h.return_value = {
                "status": "unhealthy",
                "connected": False,
                "database": "falkordb",
                "host": "remote.falkordb.cloud",
                "port": 6379,
                "graph": "anvaya",
                "ssl": True,
                "error_category": "authentication_failed",
                "error": "Authentication failed. Check credentials.",
            }
            resp = client.get("/health")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "unhealthy"
            assert data["database"] == "falkordb"
            assert data["error_category"] == "authentication_failed"
            assert "password" not in str(data).lower()

    def test_health_200_when_falkordb_connected(self):
        with patch.object(FalkorDBManager, "check_health") as mock_h:
            mock_h.return_value = {
                "status": "healthy",
                "connected": True,
                "database": "falkordb",
                "host": "remote.falkordb.cloud",
                "port": 6379,
                "graph": "anvaya",
                "ssl": True,
                "error_category": None,
                "error": None,
            }
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert data["database"] == "falkordb"


class TestCORSConfiguration:
    """Test CORS headers behavior."""

    def test_cors_preflight_request(self):
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://anvaya.vercel.app",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


class TestImpactReviewersFailClosed:
    """Test that impact_reviewers fails closed if memory constraint lookup fails."""

    def test_impact_reviewers_constraint_query_failure(self):
        real_db = FalkorDBManager()
        if not real_db.check_health().get("connected"):
            pytest.skip("FalkorDB offline")

        class FailingGraph:
            def __init__(self, g):
                self._g = g
            def query(self, q, params=None):
                if "Constraint" in q:
                    raise RuntimeError("Simulated FalkorDB memory query failure")
                return self._g.query(q, params)

        class FailingManager:
            def check_health(self):
                return {"connected": True}
            def get_graph(self, name=None):
                return FailingGraph(real_db.get_graph("anvaya_test"))

        res = impact_reviewers(
            target_file="databases/backends/postgres.py",
            repo_name="databases",
            db_manager=FailingManager(),  # type: ignore
        )
        assert res.refused is True
        assert res.reason_code == RefusalCode.NO_PATH
        assert "constraint" in res.reason.lower() or "enforcement" in res.reason.lower()
