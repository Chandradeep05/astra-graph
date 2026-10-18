from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.api.routes import health, ingest, reviewers, ask, actions, impact, impact_reviewers, context, graph

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="ANVAYA API",
    description="Graph-Powered Code Intelligence using FalkorDB",
    version="0.1.0",
)

# Configure CORS for multi-origin deployment (e.g., Vercel frontend calling Render backend)
cors_raw = settings.cors_origins.strip()
origins = [o.strip() for o in cors_raw.split(",") if o.strip()]
if not origins:
    origins = ["*"]

allow_all = origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=not allow_all,  # Do NOT use wildcard CORS together with credentials
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register health check routes
app.include_router(health.router)
app.include_router(health.router, prefix="/api/v1")

# Register ingestion routes
app.include_router(ingest.router)
app.include_router(ingest.router, prefix="/api/v1")

# Register reviewer intelligence routes
app.include_router(reviewers.router)
app.include_router(reviewers.router, prefix="/api/v1")

# Register agent ask routes
app.include_router(ask.router)
app.include_router(ask.router, prefix="/api/v1")

# Register action lifecycle routes
app.include_router(actions.router)
app.include_router(actions.router, prefix="/api/v1")

# Register blast-radius impact routes
app.include_router(impact.router)
app.include_router(impact.router, prefix="/api/v1")

# Register impact-to-reviewers routes
app.include_router(impact_reviewers.router)
app.include_router(impact_reviewers.router, prefix="/api/v1")

# Register context package routes
app.include_router(context.router)
app.include_router(context.router, prefix="/api/v1")

# Register graph exploration routes
app.include_router(graph.router)
app.include_router(graph.router, prefix="/api/v1")

# Static files for judge-facing engineering UI
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def root(request: Request):
    if "text/html" in request.headers.get("accept", ""):
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
    return {
        "service": "Anvaya API",
        "status": "online",
        "docs_url": "/docs",
        "health_url": "/health",
        "ui_url": "/",
    }


@app.get("/ui")
@app.get("/app")
def serve_ui():
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "service": "Anvaya UI",
        "status": "loading",
    }

