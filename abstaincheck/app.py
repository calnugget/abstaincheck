"""The web app: REST API under /api/v1, MCP server at /mcp, web page at /."""
import contextlib
import json
import os
import pathlib
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import mcp_server, runner
from .judge import Judge
from .modes import FORMATS
from .scoring import CATEGORIES
from .service import Service
from .store import make_store

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
ADMIN_TOKEN = os.environ.get("ABSTAINCHECK_ADMIN_TOKEN")

service = Service(make_store(), judge=Judge.from_env())
mcp = mcp_server.build(service)
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
                                  json_response=True, host="0.0.0.0")


@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="AbstainCheck API", version="1.0",
              description="Test whether an AI model can tell a safe medical question from one "
                          "with a hidden unsafe premise. Built on the AdverseMed-500 benchmark.",
              lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")
app.router.routes.extend(mcp_app.routes)


# --- a small per-address rate limit (per instance; enough to stop casual abuse) ---------

_hits = defaultdict(deque)
LIMITS = {"test": (60, 3600), "run": (10, 3600), "check": (600, 3600)}


def limit(request: Request, kind: str):
    n, window = LIMITS[kind]
    ip = (request.headers.get("x-forwarded-for") or request.client.host or "?").split(",")[0].strip()
    q, now = _hits[(kind, ip)], time.time()
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= n:
        raise HTTPException(429, "Too many requests from this address. Try again later.")
    q.append(now)


def handle(fn, *a, **k):
    try:
        return fn(*a, **k)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    except ValueError as e:
        raise HTTPException(400, str(e.args[0]))


# --- REST API -----------------------------------------------------------------------

Format = Literal[FORMATS]
FORMAT_HELP = ("standard = the paper's prompt, which tells the model it may answer FLAG; "
               "realistic = the question asked plainly, free-text reply; "
               "pressure = realistic, with the asker pushing for an answer.")
SIZE_HELP = ("quick = 40 questions, standard = 120, full = every question (760 in the standard "
             "format, the 240-question panel in the free-text formats).")


class StartTest(BaseModel):
    model_name: str = Field("", description="The model being tested.")
    size: Literal["quick", "standard", "full"] = Field("quick", description=SIZE_HELP)
    format: Format = Field("standard", description=FORMAT_HELP)
    seed: int | None = Field(None, description="Optional. Two tests with the same seed, size and "
                                               "format get the same questions, for A/B comparisons.")


class AnswerIn(BaseModel):
    handle: str
    answer: str | None = Field(None, description="A-E, or FLAG for an unsafe premise.")
    confidence: float | None = Field(None, description="0 to 1.")


class Submit(BaseModel):
    answers: list[AnswerIn]
    model_name: str = ""
    publish: bool = False


class ReplyIn(BaseModel):
    handle: str
    reply: str = Field("", description="The model's full reply, as text.")


class SubmitReplies(BaseModel):
    replies: list[ReplyIn]
    model_name: str = ""
    publish: bool = False


class Run(BaseModel):
    provider: str = Field(description=f"One of: {', '.join(runner.PROVIDERS)}")
    model: str = Field(description="The provider's model id, for example gpt-5.4-mini.")
    api_key: str = Field(description="Used for this run only; never stored or logged.")
    size: Literal["quick", "standard", "full"] = Field("quick", description=SIZE_HELP)
    format: Format = Field("standard", description=FORMAT_HELP)
    system_prompt: str = Field("", description="Optional system prompt to test, such as your app's "
                                               "safety instructions. Only a fingerprint is stored.")
    seed: int | None = None
    publish: bool = False
    display_name: str = ""


class Check(BaseModel):
    question: str
    category: Literal[CATEGORIES] | None = None
    result_id: str = ""
    model_name: str = ""


@app.get("/api/v1/health")
def health():
    return {"ok": True, "traps": len(service.bank.servable_traps), "controls": len(service.bank.controls),
            "free_text_formats": service.judge is not None}


@app.post("/api/v1/tests", summary="Start a test and get its questions (answers withheld)")
def start_test(body: StartTest, request: Request):
    limit(request, "test")
    return handle(service.start_test, size=body.size, model_name=body.model_name, mode="api_upload",
                  fmt=body.format, seed=body.seed)


@app.post("/api/v1/tests/{test_id}/answers", summary="Submit answers and get the report card")
def submit(test_id: str, body: Submit, request: Request):
    limit(request, "test")
    return handle(service.submit, test_id, [a.model_dump() for a in body.answers],
                  model_name=body.model_name or None, publish=body.publish)


@app.post("/api/v1/tests/{test_id}/replies",
          summary="Free-text formats: submit full replies; the server grades them and returns the report card")
async def submit_replies(test_id: str, body: SubmitReplies, request: Request):
    limit(request, "test")
    try:
        return await service.submit_replies(test_id, [r.model_dump() for r in body.replies],
                                            model_name=body.model_name or None, publish=body.publish)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    except ValueError as e:
        raise HTTPException(400, str(e.args[0]))


@app.post("/api/v1/runs", summary="Verified run: AbstainCheck queries your model with your key",
          description="Streams newline-delimited JSON progress events; the last is `done` with "
                      "the report, or `error`. Your key is used for this run only.")
async def run(body: Run, request: Request):
    limit(request, "run")

    async def events():
        async for ev in runner.run(service, body.provider, body.model.strip(), body.api_key.strip(),
                                   size=body.size, publish=body.publish,
                                   display_name=body.display_name.strip() or None,
                                   fmt=body.format, system_prompt=body.system_prompt,
                                   seed=body.seed):
            yield json.dumps(ev) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"cache-control": "no-store", "x-accel-buffering": "no"})


@app.get("/api/v1/providers")
def providers():
    return [{"id": k, "label": v[0]} for k, v in runner.PROVIDERS.items()]


@app.get("/api/v1/results/{result_id}", summary="A report card")
def result(result_id: str):
    return handle(service.report, result_id)


@app.post("/api/v1/results/{result_id}/publish")
def publish(result_id: str, publish: bool = True):
    return handle(service.publish, result_id, publish)


@app.get("/api/v1/leaderboard")
def leaderboard():
    return service.leaderboard()


@app.post("/api/v1/check", summary="Checklist and personal caution level for a medical question")
def check(body: Check, request: Request):
    limit(request, "check")
    return handle(service.check_question, body.question, category=body.category,
                  result_id=body.result_id or None, model_name=body.model_name or None)


def admin_only(request: Request):
    if not ADMIN_TOKEN or request.headers.get("authorization") != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(403, "forbidden")


@app.delete("/api/v1/admin/results/{result_id}", include_in_schema=False)
def admin_delete(result_id: str, request: Request):
    admin_only(request)
    service.store.update("results", result_id, {"published": False})
    return {"unpublished": result_id}


@app.get("/api/v1/admin/usage", include_in_schema=False)
def admin_usage(request: Request, days: int = 30):
    """Anonymous daily counts (tests started, results, checks, replies graded)."""
    admin_only(request)
    return service.usage(min(max(days, 1), 366))


# --- web page -----------------------------------------------------------------------

@app.get("/", include_in_schema=False)
@app.get("/r/{result_id}", include_in_schema=False)
def index(result_id: str | None = None):
    return FileResponse(WEB / "index.html")


@app.get("/privacy", include_in_schema=False)
def privacy():
    return FileResponse(WEB / "privacy.html")


@app.get("/methods", include_in_schema=False)
def methods():
    return FileResponse(WEB / "methods.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
