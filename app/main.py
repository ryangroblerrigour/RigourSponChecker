"""RigourSponCheck: codes spontaneous brand awareness open ends against a code frame."""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from . import db
from .matcher import available_codeframes, code_text, get_codeframe

log = logging.getLogger("sponcheck")

API_KEY = os.getenv("API_KEY", "")
DEFAULT_CODEFRAME = os.getenv("DEFAULT_CODEFRAME", "parcel_carriers_uk")
MAX_TEXT = 2000
MAX_ANSWERS = 10

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="RigourSponCheck", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)


def require_key(x_api_key: str = Header(default="")) -> None:
    if not API_KEY or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class CodeRequest(BaseModel):
    project: str = Field(min_length=1, max_length=100)
    respid: str = Field(min_length=1, max_length=100)
    text: str | None = Field(default=None, description="One big text box answer")
    answers: list[str] | None = Field(default=None, description="Up to 10 separate answers")
    codeframe: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.answers is not None and len(self.answers) > MAX_ANSWERS:
            raise ValueError(f"answers can contain at most {MAX_ANSWERS} items")
        if not (self.text or self.answers):
            raise ValueError("provide text or answers")
        return self

    def combined(self) -> str:
        parts = [self.text] if self.text else []
        parts += [a for a in (self.answers or []) if a and a.strip()]
        return "\n".join(parts)[:MAX_TEXT]


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}


@app.post("/code", dependencies=[Depends(require_key)])
def code(req: CodeRequest):
    name = req.codeframe or DEFAULT_CODEFRAME
    try:
        cf = get_codeframe(name)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"Unknown codeframe '{name}'")

    text = req.combined()
    results = [r.as_dict() for r in code_text(text, cf)]
    codes: list[int] = []
    for r in results:
        if r["code"] is not None and r["code"] not in codes:
            codes.append(r["code"])

    response = {
        "project": req.project,
        "respid": req.respid,
        "codeframe": cf.name,
        "results": results,
        "codes": codes,
        "codes_string": ",".join(str(c) for c in codes),
        "unmatched_count": sum(1 for r in results if r["code"] is None),
    }
    try:
        db.log_request(req.project, req.respid, cf.name, text, response)
    except Exception:  # logging must never block the survey
        log.exception("Failed to log request")
    return response


@app.get("/codeframes", dependencies=[Depends(require_key)])
def codeframes():
    return {"codeframes": available_codeframes(), "default": DEFAULT_CODEFRAME}


@app.get("/codeframes/{name}", dependencies=[Depends(require_key)])
def codeframe_detail(name: str):
    try:
        cf = get_codeframe(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")
    return {"name": cf.name, "codes": [{"code": c, "label": l} for c, l in sorted(cf.labels.items())]}


@app.get("/unmatched", dependencies=[Depends(require_key)])
def unmatched(project: str | None = None, limit: int = Query(default=100, le=1000)):
    return {"project": project, "unmatched": db.unmatched(project, limit)}


@app.get("/respondent/{project}/{respid}", dependencies=[Depends(require_key)])
def respondent(project: str, respid: str):
    return {"project": project, "respid": respid, "requests": db.respondent(project, respid)}
