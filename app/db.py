"""Request logging. Uses DATABASE_URL (Postgres on Render), or a local SQLite file."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine, func, select,
)


def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./sponcheck.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


engine = create_engine(_db_url(), pool_pre_ping=True, future=True)
meta = MetaData()

requests_t = Table(
    "sponcheck_requests", meta,
    Column("id", Integer, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("project", String(100), nullable=False, index=True),
    Column("respid", String(100), nullable=False, index=True),
    Column("codeframe", String(100), nullable=False),
    Column("input_text", Text, nullable=False),
    Column("codes_string", String(500)),
    Column("response_json", Text),
)

mentions_t = Table(
    "sponcheck_mentions", meta,
    Column("id", Integer, primary_key=True),
    Column("request_id", Integer, ForeignKey("sponcheck_requests.id"), index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("project", String(100), nullable=False, index=True),
    Column("respid", String(100), nullable=False),
    Column("codeframe", String(100), nullable=False),
    Column("verbatim", String(500), nullable=False),
    Column("code", Integer),
    Column("match", String(20), nullable=False),
)


_ready = False


def init_db() -> bool:
    """Create tables if needed. Returns False instead of crashing if the DB is not up yet."""
    global _ready
    if _ready:
        return True
    try:
        meta.create_all(engine)
        _ready = True
    except Exception:
        logging.getLogger("sponcheck").warning("Database not reachable yet, will retry on first request")
    return _ready


def log_request(project: str, respid: str, codeframe: str, text: str, response: dict) -> None:
    if not init_db():
        raise RuntimeError("Database not reachable")
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        req_id = conn.execute(requests_t.insert().values(
            created_at=now, project=project, respid=respid, codeframe=codeframe,
            input_text=text, codes_string=response.get("codes_string"),
            response_json=json.dumps(response),
        )).inserted_primary_key[0]
        rows = [
            dict(request_id=req_id, created_at=now, project=project, respid=respid,
                 codeframe=codeframe, verbatim=r["verbatim"][:500], code=r["code"], match=r["match"])
            for r in response["results"]
        ]
        if rows:
            conn.execute(mentions_t.insert(), rows)


def unmatched(project: str | None, limit: int) -> list[dict]:
    """Most common verbatims that came back NULL, to help grow the alias lists."""
    v = func.lower(mentions_t.c.verbatim)
    q = (select(v.label("verbatim"), mentions_t.c.match, func.count().label("n"))
         .where(mentions_t.c.code.is_(None))
         .group_by(v, mentions_t.c.match)
         .order_by(func.count().desc())
         .limit(limit))
    if project:
        q = q.where(mentions_t.c.project == project)
    init_db()
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(q)]


def respondent(project: str, respid: str) -> list[dict]:
    q = (select(requests_t.c.created_at, requests_t.c.input_text, requests_t.c.codes_string)
         .where(requests_t.c.project == project, requests_t.c.respid == respid)
         .order_by(requests_t.c.created_at.desc()))
    init_db()
    with engine.connect() as conn:
        return [
            {**dict(r._mapping), "created_at": r.created_at.isoformat()}
            for r in conn.execute(q)
        ]
