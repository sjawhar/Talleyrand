"""
Main application entry point for the Talleyrand backend API.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from talleyrand.core.config import settings
from talleyrand.core.llm import StructuredGenerationError
from talleyrand.features.app_config.router import router as app_config_router
from talleyrand.features.auth_jwt.router import router as auth_jwt_router
from talleyrand.features.demo_cases.seed import seed_demo_cases
from talleyrand.features.graph.router import router as graph_router
from talleyrand.features.oauth_google.app import router as oauth_google_router
from talleyrand.features.research.generation.manager import manager as generation_manager
from talleyrand.features.research.router import router as research_router
from talleyrand.features.share.router import router as share_router
from talleyrand.features.transcription.router import router as transcription_router
from talleyrand.infra.body_limit import BodySizeLimitMiddleware
from talleyrand.infra.db import lifespan_db
from talleyrand.infra.log_redaction import install_query_string_redaction

logging.basicConfig(level=settings.logging_level)

# Suppress verbose third-party debug/info logs
logging.getLogger("pymongo").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# Request URLs are logged; query strings can carry credentials. Never log them.
install_query_string_redaction()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup: Initialize MongoDB and seed the landing page's shared demo cases
    async with lifespan_db() as (_, db):
        await seed_demo_cases(db)
        yield
        # Shutdown: stop generation tasks; MongoDB client is closed automatically
        await generation_manager.shutdown()


app = FastAPI(
    title="Talleyrand API",
    description="Backend API for Talleyrand application",
    version="0.7.0",
    root_path="/backend",
    lifespan=lifespan,
)

# Reject oversized request bodies before they are buffered in memory.
# Added before CORS so CORS stays outermost and browsers can read the 413.
app.add_middleware(BodySizeLimitMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Local mode signs in anyone who can reach the server, so it must answer only
# requests addressed to this machine: a page on another name that re-resolves to
# 127.0.0.1 (DNS rebinding) is same-origin with that name and could otherwise
# sign in and read every case. Its requests carry the foreign name in Host.
# The host check ignores the port. Outermost, so nothing else runs first.
if settings.local_user_email is not None:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


# A failed structured LLM call is the caller's provider problem (bad key,
# quota, refusal), not a server bug: answer with the provider's message as a
# 400 instead of a 500, uniformly for every endpoint that generates.
@app.exception_handler(StructuredGenerationError)
async def structured_generation_error_handler(
    request: Request, exc: StructuredGenerationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(oauth_google_router)
app.include_router(graph_router)
app.include_router(auth_jwt_router)
app.include_router(app_config_router)
app.include_router(share_router)
app.include_router(research_router)
app.include_router(transcription_router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
