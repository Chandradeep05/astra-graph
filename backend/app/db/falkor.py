from typing import Any, Dict, Optional
import logging
from falkordb import FalkorDB

from app.core.config import settings

logger = logging.getLogger(__name__)


class FalkorDBManager:
    """
    Manages connections to FalkorDB and provides health checks
    and graph access.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
        graph_name: Optional[str] = None,
    ):
        self.host = host or settings.falkordb_host
        self.port = port or settings.falkordb_port
        self.password = password if password is not None else settings.falkordb_password
        self.graph_name = graph_name or settings.falkordb_graph_name
        self._client: Optional[FalkorDB] = None

    def get_client(self) -> FalkorDB:
        """
        Lazily creates or returns the active FalkorDB client.
        """
        if self._client is None:
            self._client = FalkorDB(
                host=self.host,
                port=self.port,
                password=self.password,
            )
        return self._client

    def get_graph(self, name: Optional[str] = None):
        """
        Returns a FalkorDB Graph object for the specified graph name.
        """
        target_name = name or self.graph_name
        client = self.get_client()
        return client.select_graph(target_name)

    def check_health(self) -> Dict[str, Any]:
        """
        Pings FalkorDB and runs a minimal Cypher test (RETURN 1 AS ping).
        Returns a structured dictionary indicating connectivity status.
        """
        try:
            client = self.get_client()
            # Select default graph and verify Cypher execution
            graph = client.select_graph(self.graph_name)
            result = graph.query("RETURN 1 AS ping")

            if result and len(result.result_set) > 0 and result.result_set[0][0] == 1:
                return {
                    "status": "healthy",
                    "connected": True,
                    "host": self.host,
                    "port": self.port,
                    "graph": self.graph_name,
                    "error": None,
                }
            else:
                return {
                    "status": "unhealthy",
                    "connected": False,
                    "host": self.host,
                    "port": self.port,
                    "graph": self.graph_name,
                    "error": "Unexpected response from FalkorDB ping query",
                }
        except Exception as exc:
            logger.warning("FalkorDB health check failed: %s", exc)
            # Reset client on failure so future attempts reconnect cleanly
            self._client = None
            return {
                "status": "unhealthy",
                "connected": False,
                "host": self.host,
                "port": self.port,
                "graph": self.graph_name,
                "error": str(exc),
            }

    def close(self):
        """
        Closes FalkorDB connection if open.
        """
        self._client = None


# Singleton instance for application dependency injection
falkor_manager = FalkorDBManager()


def get_falkor_manager() -> FalkorDBManager:
    return falkor_manager
