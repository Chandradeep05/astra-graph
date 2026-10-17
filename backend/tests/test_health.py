import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.falkor import FalkorDBManager


client = TestClient(app)


def test_health_endpoint_healthy():
    """
    Test GET /health when FalkorDB is reachable and healthy.
    Should return 200 OK with health status and graph info.
    """
    with patch.object(FalkorDBManager, "check_health") as mock_health:
        mock_health.return_value = {
            "status": "healthy",
            "connected": True,
            "host": "localhost",
            "port": 6379,
            "graph": "anvaya",
            "version": "test-version",
            "error": None,
        }

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["falkordb"]["connected"] is True
        assert data["falkordb"]["graph"] == "anvaya"


def test_health_endpoint_unhealthy_when_falkordb_down():
    """
    Test GET /health when FalkorDB is unreachable.
    Should return 503 Service Unavailable with unhealthy status and error details.
    """
    with patch.object(FalkorDBManager, "check_health") as mock_health:
        mock_health.return_value = {
            "status": "unhealthy",
            "connected": False,
            "host": "localhost",
            "port": 6379,
            "graph": "anvaya",
            "version": None,
            "error": "Connection refused to FalkorDB on localhost:6379",
        }

        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["falkordb"]["connected"] is False
        assert "Connection refused" in data["falkordb"]["error"]


def test_falkordb_manager_real_connection_if_available():
    """
    Integration test: verify FalkorDB manager against a real running FalkorDB instance.
    If FalkorDB is running on localhost:6379, check_health must report healthy and ping Cypher.
    """
    manager = FalkorDBManager()
    health = manager.check_health()
    if health["connected"]:
        assert health["status"] == "healthy"
        assert health["connected"] is True
        # Verify query against actual FalkorDB instance
        graph = manager.get_graph()
        result = graph.query("RETURN 1 AS ping")
        assert len(result.result_set) == 1
        assert result.result_set[0][0] == 1
    else:
        # If not connected during isolated unit testing, error must be captured
        assert health["status"] == "unhealthy"
        assert health["error"] is not None
