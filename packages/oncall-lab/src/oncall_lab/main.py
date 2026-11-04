"""Operator lab for the Astronomy Shop.

This process can arm a fault in the flag file and can post one symptom to the
incident API. It does not run the agent and it does not share the incident
store. The responder console stays on the incident service.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .catalog import SYMPTOMS, CatalogError, LabFault, Symptom, fault_view, load_catalog
from .flags import (
    FlagEditError,
    FlagFileError,
    current_variant,
    read_document,
    set_variant,
    write_document,
)
from .paging import notification

STATIC = Path(__file__).with_name("static")
PostJson = Callable[[str, dict[str, Any]], Any]


class PageBody(BaseModel):
    tool_budget: int = Field(default=10, ge=1, le=100)


class UpstreamError(RuntimeError):
    """The incident API refused the page or could not be reached."""


def post_json(url: str, body: dict[str, Any], timeout: float = 10.0) -> Any:
    request = Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read(500).decode(errors="replace")
        raise UpstreamError(f"incident API returned HTTP {exc.code}: {detail}") from exc
    except (OSError, TimeoutError, URLError) as exc:
        raise UpstreamError(f"incident API request failed: {exc}") from exc
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpstreamError("incident API returned non-JSON") from exc
    return parsed


def alerts_endpoint(base: str, tool_budget: int) -> str:
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["tool_budget"] = str(tool_budget)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _incident_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("incident_id"), str):
        return payload["incident_id"]
    incidents = payload.get("incidents")
    if not isinstance(incidents, list) or not incidents or not isinstance(incidents[0], dict):
        return None
    incident_id = incidents[0].get("incident_id")
    return incident_id if isinstance(incident_id, str) else None


def create_app(
    *,
    manifest_path: Path | None = None,
    flag_path: Path | None = None,
    alerts_url: str | None = None,
    console_url: str | None = None,
    poster: PostJson | None = None,
    clock: Callable[[], str] | None = None,
    symptoms: dict[str, Symptom] | None = None,
) -> FastAPI:
    manifest = manifest_path or Path(os.environ.get("ONCALL_MANIFEST", "eval/faults/manifest.yaml"))
    flags = flag_path or Path(os.environ.get("FLAGD_CONFIG", "deploy/flagd/demo.flagd.json"))
    alerts = alerts_url or os.environ.get("ONCALL_ALERTS_URL", "http://127.0.0.1:8000/alerts")
    console = console_url or os.environ.get("ONCALL_CONSOLE_URL", "http://127.0.0.1:8000/")
    send = poster or (lambda url, body: post_json(url, body))
    timestamp = clock or _now
    published = SYMPTOMS if symptoms is None else symptoms

    app = FastAPI(title="On-Call Copilot Lab", version="0.1.0")
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.exception_handler(CatalogError)
    async def catalog_error(_request, exc: CatalogError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(FlagFileError)
    async def flag_file_error(_request, exc: FlagFileError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(FlagEditError)
    async def flag_edit_error(_request, exc: FlagEditError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(UpstreamError)
    async def upstream_error(_request, exc: UpstreamError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    def catalog() -> tuple[LabFault, ...]:
        return load_catalog(manifest, published)

    def one(fault_id: str) -> LabFault:
        for fault in catalog():
            if fault.fault_id == fault_id:
                return fault
        raise HTTPException(status_code=404, detail=f"unknown fault {fault_id!r}")

    def view(fault: LabFault) -> dict[str, Any]:
        document = read_document(flags)
        variant = current_variant(document, fault.flag)
        return fault_view(fault, variant)

    def apply(fault: LabFault, variant: str) -> dict[str, Any]:
        document = read_document(flags)
        updated, changed = set_variant(document, fault.flag, variant)
        if changed:
            write_document(flags, updated)
        return {"changed": changed, "fault": fault_view(fault, variant)}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        catalog()
        read_document(flags)
        return {"status": "ok"}

    @app.get("/api/faults")
    def list_faults() -> dict[str, Any]:
        return {
            "topology": "c1_single",
            "tool_budget_default": 10,
            "tool_budget_min": 1,
            "tool_budget_max": 100,
            "console_url": console,
            "faults": [view(fault) for fault in catalog()],
        }

    @app.post("/api/faults/{fault_id}/arm")
    def arm(fault_id: str) -> dict[str, Any]:
        fault = one(fault_id)
        return apply(fault, fault.arm_variant)

    @app.post("/api/faults/{fault_id}/disarm")
    def disarm(fault_id: str) -> dict[str, Any]:
        fault = one(fault_id)
        return apply(fault, fault.disarm_variant)

    @app.post("/api/faults/{fault_id}/page")
    def page(fault_id: str, body: PageBody) -> dict[str, Any]:
        fault = one(fault_id)
        if fault.symptom is None:
            raise HTTPException(
                status_code=422,
                detail=f"no symptom is published for {fault_id!r}",
            )
        alert = notification(fault.symptom, timestamp())
        payload = send(alerts_endpoint(alerts, body.tool_budget), alert)
        return {
            "fault_id": fault.fault_id,
            "tool_budget": body.tool_budget,
            "alert": alert,
            "incident_id": _incident_id(payload),
            "incident_api": payload,
        }

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "oncall_lab.main:app",
        host=os.environ.get("ONCALL_LAB_HOST", "127.0.0.1"),
        port=int(os.environ.get("ONCALL_LAB_PORT", "8020")),
        reload=False,
    )
