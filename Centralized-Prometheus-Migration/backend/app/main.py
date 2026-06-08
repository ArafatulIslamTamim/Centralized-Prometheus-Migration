from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.migration_routes import router as migration_router

load_dotenv()

app = FastAPI(
    title="Prometheus Migration Control API",
    version="0.1.0",
    description="FastAPI backend for controlled LM1 to LM2 Prometheus TSDB migration.",
)

frontend_origin = os.getenv(
    "FRONTEND_ORIGIN",
    "https://centralized-prometheus-migration-frontend.onrender.com",
)

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=[
#         frontend_origin,
#         "https://centralized-prometheus-migration-frontend.onrender.com",
#         "http://localhost:3001",
#         "http://localhost:3000",
#         "http://127.0.0.1:3001",
#     ],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(migration_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "Prometheus Migration Control API",
        "docs": "/docs",
        "health": "/api/migration/health",
    }