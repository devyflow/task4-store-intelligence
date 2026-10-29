from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import StoreAgent
from analysis import write_metrics
from build_database import build
from data_api import DataAPI, STORES

PROJECT = Path(__file__).resolve().parent
DATABASE = PROJECT / "analytics.sqlite3"
STATIC = PROJECT / "static"
METRICS = PROJECT / "outputs" / "metrics.json"

if not DATABASE.exists():
    build(PROJECT / "data", DATABASE)

if not METRICS.exists():
    write_metrics(PROJECT / "data", METRICS)

data_api = DataAPI(DATABASE)


@lru_cache(maxsize=1)
def get_agent() -> StoreAgent:
    return StoreAgent(data_api)


app = FastAPI(title="Store Intelligence", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    current_store: str = Field(default="STORE1")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/metrics")
def metrics():
    if not METRICS.exists():
        raise HTTPException(503, "Metrics have not been generated")
    return json.loads(METRICS.read_text(encoding="utf-8"))


@app.get("/api/overview/{store}")
def overview(store: str):
    try:
        return data_api.overview(store)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/users/{store}")
def users(
    store: str,
    email_prefix: list[str] = Query(default=[]),
    name_contains: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
):
    try:
        return data_api.users(store, email_prefix, name_contains, limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/books/{store}")
def books(
    store: str,
    title_contains: str | None = None,
    author_contains: str | None = None,
    person_name_filter: str = "any",
    limit: int = Query(default=25, ge=1, le=100),
):
    try:
        return data_api.books(store, title_contains, author_contains, person_name_filter, limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/chat")
def chat(request: ChatRequest):
    if request.current_store.upper() not in STORES:
        raise HTTPException(400, "Unknown store")
    try:
        return get_agent().respond(request.message, request.current_store.upper())
    except Exception as exc:
        raise HTTPException(502, f"The data agent could not complete the request: {exc}") from exc


@app.get("/health")
def health():
    return {"status": "ok", "stores": list(STORES), "database": DATABASE.exists()}
