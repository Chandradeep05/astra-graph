from typing import Any, Dict, Optional
import logging
from falkordb import FalkorDB

from app.core.config import settings

logger = logging.getLogger(__name__)


def classify_falkordb_error(exc: Exception) -> Dict[str, str]:
    """
    Classifies a FalkorDB / Redis connection or query error into a safe diagnostic category.
    Sanitizes sensitive information (passwords, tokens, credentials, URIs).
    Never exposes credentials or secrets.
    """
    err_str = str(exc)
    err_lower = err_str.lower()
    exc_type = type(exc).__name__

    # 1. DNS / Host resolution failure
    if any(k in err_lower for k in ["getaddrinfo failed", "name or service not known", "nodename nor servname"]):
        category = "dns_resolution_failed"
        detail = "Failed to resolve database host name. Check FALKORDB_HOST."
    # 3 & 7. Authentication / Invalid credentials
    elif any(k in err_lower for k in ["noauth", "wrongpass", "invalid password", "authentication failed", "auth failed"]):
        category = "authentication_failed"
        detail = "Authentication failed. Check FALKORDB_USERNAME and FALKORDB_PASSWORD."
    # 4. TLS / SSL failure
    elif any(k in err_lower for k in ["certificate_verify_failed", "ssl", "tlsv1", "handshake failed", "wrong_version_number"]):
        category = "tls_ssl_handshake_failed"
        detail = "TLS/SSL handshake failed. Check FALKORDB_SSL setting and certificate validity."
    # 8. Timeout
    elif any(k in err_lower for k in ["timeout", "timed out", "connection timed out"]):
        category = "connection_timeout"
        detail = "Connection to FalkorDB timed out. Check network routing and firewalls."
    # 2. TCP connection failure
    elif any(k in err_lower for k in ["connection refused", "actively refused", "10061", "111", "cannot assign requested address"]):
        category = "tcp_connection_refused"
        detail = "TCP connection refused on host/port. Verify FalkorDB service is running and port is open."
    # 6. Graph initialization / does not exist
    elif "graph does not exist" in err_lower:
        category = "graph_not_found"
        detail = "Graph does not exist in FalkorDB. Run repository ingestion to initialize."
    # 5. Protocol / client incompatibility
    elif any(k in err_lower for k in ["syntax error", "unknown command", "unknown module"]):
        category = "protocol_incompatible"
        detail = "FalkorDB protocol or command incompatibility."
    else:
        category = "database_unavailable"
        detail = f"FalkorDB unavailable ({exc_type})."

    return {
        "error_category": category,
        "detail": detail,
    }


class FalkorDBManager:
    """
    Manages connections to FalkorDB and provides health checks
    and graph access. Supports local Docker, remote FalkorDB Cloud,
    and SSL/TLS authenticated deployments.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        graph_name: Optional[str] = None,
        ssl: Optional[bool] = None,
        url: Optional[str] = None,
    ):
        raw_host = host or settings.falkordb_host
        raw_port = port or settings.falkordb_port
        if raw_host and ":" in raw_host and not raw_host.startswith("["):
            parts = raw_host.split(":")
            raw_host = parts[0]
            if len(parts) > 1 and parts[1].isdigit():
                raw_port = int(parts[1])
        self.host = raw_host
        self.port = raw_port
        self.username = username if username is not None else settings.falkordb_username
        self.password = password if password is not None else settings.falkordb_password
        self.graph_name = graph_name or settings.falkordb_graph_name
        self.ssl = ssl if ssl is not None else settings.falkordb_ssl
        self.url = url or settings.falkordb_url
        self._client: Optional[FalkorDB] = None

    def get_client(self) -> FalkorDB:
        """
        Lazily creates or returns the active FalkorDB client.
        Uses FALKORDB_URL if provided, else connects via individual parameters.
        """
        if self._client is None:
            if self.url:
                self._client = FalkorDB.from_url(self.url)
            else:
                self._client = FalkorDB(
                    host=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    ssl=self.ssl,
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
        Returns a structured dictionary indicating connectivity status and sanitized diagnostics.
        Never exposes passwords, tokens, or credentials.
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
                    "database": "falkordb",
                    "host": self.host,
                    "port": self.port,
                    "graph": self.graph_name,
                    "ssl": self.ssl,
                    "error_category": None,
                    "error": None,
                }
            else:
                return {
                    "status": "unhealthy",
                    "connected": False,
                    "database": "falkordb",
                    "host": self.host,
                    "port": self.port,
                    "graph": self.graph_name,
                    "ssl": self.ssl,
                    "error_category": "invalid_ping_response",
                    "error": "Unexpected response from FalkorDB ping query",
                }
        except Exception as exc:
            logger.warning("FalkorDB health check failed: %s", exc)
            # Reset client on failure so future attempts reconnect cleanly
            self._client = None
            diag = classify_falkordb_error(exc)
            return {
                "status": "unhealthy",
                "connected": False,
                "database": "falkordb",
                "host": self.host,
                "port": self.port,
                "graph": self.graph_name,
                "ssl": self.ssl,
                "error_category": diag["error_category"],
                "error": diag["detail"],
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
