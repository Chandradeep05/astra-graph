from fastapi import FastAPI
from app.core.config import settings
from app.api.routes import health

app = FastAPI(
    title="ANVAYA API",
    description="Graph-Powered Code Intelligence using FalkorDB",
    version="0.1.0",
)

# Register health check routes
app.include_router(health.router)
app.include_router(health.router, prefix="/api/v1")


@app.get("/")
def root():
    return {
        "service": "Anvaya API",
        "status": "online",
        "docs_url": "/docs",
        "health_url": "/health",
    }
