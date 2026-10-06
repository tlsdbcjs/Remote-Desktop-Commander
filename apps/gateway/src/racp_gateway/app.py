import asyncio
import ipaddress
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, ValidationError
from racp_domain.jobs import JobState
from racp_domain.models import TERMINAL_STATES, RACPError
from racp_protocol.artifacts import TransferComplete, TransferCreate
from racp_protocol.console import (
    ApprovalView,
    ArtifactView,
    AuditView,
    ConsoleLogin,
    ConsoleSession,
    DeviceView,
    DoctorView,
    ListPage,
)
from racp_protocol.jobs import JobView
from racp_protocol.models import (
    Ack,
    BrowserState,
    BrowserStateAck,
    Heartbeat,
    Hello,
    JobUpdate,
    OperationInput,
    OutcomeExpired,
    Reconcile,
    Result,
    StrictModel,
    Welcome,
    decode_message,
)
from racp_protocol.registry import REGISTRY
from racp_protocol.streams import StreamData, StreamEnd, StreamGap, StreamOpened
from racp_sdk.connection_file import ConnectionFile, validate_ca_pem
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from racp_gateway.artifacts import ArtifactManager
from racp_gateway.console_auth import COOKIE, ConsoleAuth
from racp_gateway.events import EventFeed, EventResponse
from racp_gateway.lists import ConsoleLists
from racp_gateway.mcp import create_mcp
from racp_gateway.network import checked_origin
from racp_gateway.oauth import OAuthResourceConfig, OAuthTokenVerifier
from racp_gateway.service import Connection, ControlPlane
from racp_gateway.store import GatewayStore
from racp_gateway.terminal_socket import terminal_socket

HTTP_ERRORS = {
    "INVALID_ARGUMENT": 400,
    "UNAUTHENTICATED": 401,
    "SESSION_EXPIRED": 401,
    "PERMISSION_DENIED": 403,
    "DEVICE_REVOKED": 403,
    "OPERATION_NOT_FOUND": 404,
    "OPERATION_EXPIRED": 410,
    "JOB_NOT_FOUND": 404,
    "ARTIFACT_NOT_FOUND": 404,
    "ARTIFACT_EXPIRED": 410,
    "HANDLE_EXPIRED": 410,
    "CURSOR_EXPIRED": 410,
    "IDEMPOTENCY_CONFLICT": 409,
    "CONFLICT": 409,
    "APPROVAL_REQUIRED": 409,
    "APPROVAL_EXPIRED": 410,
    "RESOURCE_EXHAUSTED": 429,
    "DEVICE_OFFLINE": 503,
    "TIMEOUT": 504,
    "CAPABILITY_UNAVAILABLE": 422,
    "EXECUTION_UNKNOWN": 503,
    "CHECKSUM_MISMATCH": 412,
}


class EnrollmentInput(StrictModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f]+$")
    include_connection_file: bool = False


class EnrollInput(StrictModel):
    token: str = Field(min_length=20, max_length=128)


def bearer(request: Request | WebSocket) -> str:
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer "):
        raise RACPError("UNAUTHENTICATED", "bearer authentication required")
    return value[7:]


def create_app(
    data_dir: Path,
    *,
    trusted_personal: bool = False,
    approvals_enabled: bool = True,
    console_dir: Path | None = None,
    public_origin: str | None = None,
    oauth_config: OAuthResourceConfig | None = None,
    client_ca_pem: str | None = None,
    local_mcp_port: int | None = None,
) -> FastAPI:
    if public_origin is not None:
        public_origin = checked_origin(public_origin)
    if client_ca_pem is not None:
        if len(client_ca_pem.encode("utf-8")) > 16384:
            raise ValueError("Client CA exceeds its bound")
        client_ca_pem = validate_ca_pem(client_ca_pem)
    allowed_hosts = ["127.0.0.1", "localhost", "[::1]", "testserver"]
    if public_origin is not None:
        allowed_hosts = [urlsplit(public_origin).hostname or ""]
    if oauth_config is not None and public_origin is None:
        raise ValueError("OAuth MCP requires an explicit public origin")
    local_mcp_origin = None
    if local_mcp_port is not None:
        if not 1 <= local_mcp_port <= 65535 or oauth_config is None:
            raise ValueError("Local MCP listener requires a valid port and configured OAuth")
        if not public_origin or not public_origin.startswith("https://"):
            raise ValueError("Local MCP listener requires direct HTTPS")
        local_mcp_origin = checked_origin(f"https://127.0.0.1:{local_mcp_port}")
        allowed_hosts.append("127.0.0.1")
    oauth = (
        OAuthTokenVerifier(oauth_config, local_mcp_origin or public_origin or "")
        if oauth_config
        else None
    )
    store = GatewayStore(data_dir / "gateway.db")
    store.initialize()
    console_auth = ConsoleAuth(store)
    events = EventFeed(store)
    store.event_feed = events
    lists = ConsoleLists(store)
    control = ControlPlane(
        store, trusted_personal=trusted_personal, approvals_enabled=approvals_enabled
    )
    artifacts = ArtifactManager(data_dir / "artifacts", store)
    control.artifacts = artifacts
    mcp = create_mcp(control, oauth)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        max_request_body_size=1024 * 1024,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[value for host in allowed_hosts for value in [host, host + ":*"]],
            allowed_origins=[local_mcp_origin or public_origin]
            if public_origin
            else [
                scheme + "://" + host + ":*"
                for scheme in ["http", "https"]
                for host in allowed_hosts
            ],
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async def garbage_collection() -> None:
            while True:
                await asyncio.sleep(60)
                await artifacts.collect()
                store.compact()
                console_auth.collect()
                events.collect()

        collector = asyncio.create_task(garbage_collection())
        control.scheduler.start()
        try:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)
            await control.scheduler.close()
            for connection in list(control.connections.values()):
                await connection.socket.close(code=1001)
            store.close()

    app = FastAPI(title="RACP Gateway", version="0.1.0", lifespan=lifespan)
    app.state.control = control
    app.state.artifacts = artifacts
    app.state.console_auth = console_auth
    app.state.events = events
    app.state.lists = lists
    app.state.oauth = oauth

    def socket_origin_matches(socket: WebSocket) -> bool:
        try:
            server = socket.scope.get("server")
            if local_mcp_port and server and server[1] == local_mcp_port:
                return False
            actual = socket.url.replace(
                scheme="https" if socket.url.scheme == "wss" else "http", path="", query=""
            )
            return checked_origin(str(actual)) == public_origin
        except ValueError:
            return False

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.exception_handler(RACPError)
    async def handle_error(request: Request, exc: RACPError) -> JSONResponse:
        return JSONResponse(
            {"error": asdict(exc.error)}, status_code=HTTP_ERRORS.get(exc.error.code, 500)
        )

    @app.middleware("http")
    async def ingress(request: Request, call_next: Any) -> Any:
        transfer_body = (
            request.method == "PUT"
            and request.url.path.startswith("/api/v1/artifact-transfers/")
            and request.url.path.endswith("/content")
        )
        if not transfer_body and request.url.path.startswith(("/api/", "/mcp", "/agent/v1/enroll")):
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1024 * 1024:
                    return JSONResponse(
                        {
                            "error": asdict(
                                RACPError("INVALID_ARGUMENT", "request body exceeds 1 MiB").error
                            )
                        },
                        status_code=400,
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        origin = request.headers.get("origin")
        mcp_route = request.url.path == "/mcp" or request.url.path.startswith("/mcp/")
        metadata_route = request.url.path in {
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        }
        try:
            actual_origin = checked_origin(str(request.base_url))
            if local_mcp_origin and actual_origin == local_mcp_origin:
                server = request.scope.get("server")
                origin_matches = bool(
                    request.client
                    and ipaddress.ip_address(request.client.host).is_loopback
                    and server
                    and ipaddress.ip_address(server[0]).is_loopback
                    and server[1] == local_mcp_port
                    and (mcp_route or metadata_route)
                )
            else:
                server = request.scope.get("server")
                origin_matches = (not public_origin or actual_origin == public_origin) and not (
                    local_mcp_origin
                    and (mcp_route or metadata_route or (server and server[1] == local_mcp_port))
                )
        except ValueError:
            origin_matches = False
        if not origin_matches:
            return JSONResponse(
                {"error": asdict(RACPError("PERMISSION_DENIED", "public origin mismatch").error)},
                status_code=403,
            )
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse(
                {"error": asdict(RACPError("PERMISSION_DENIED", "origin denied").error)},
                status_code=403,
            )
        if request.url.path.startswith("/mcp") and oauth is None:
            local_peer = local_origin = False
            try:
                local_peer = (
                    bool(request.client)
                    and ipaddress.ip_address(
                        request.client.host if request.client else ""
                    ).is_loopback
                )
                public_host = urlsplit(public_origin).hostname if public_origin else None
                local_origin = (
                    public_host in {None, "localhost"}
                    or ipaddress.ip_address(public_host or "").is_loopback
                )
            except ValueError:
                pass
            if not local_peer or not local_origin:
                return JSONResponse(
                    {
                        "error": asdict(
                            RACPError(
                                "CAPABILITY_UNAVAILABLE", "Remote MCP requires configured OAuth"
                            ).error
                        )
                    },
                    status_code=503,
                )
            try:
                store.owner(bearer(request))
            except RACPError as exc:
                return JSONResponse({"error": asdict(exc.error)}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith(("/api/", "/events", "/console")):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/console"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; "
                "form-action 'self'"
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "error": asdict(
                    RACPError("INVALID_ARGUMENT", "request does not match the input schema").error
                )
            },
            status_code=400,
        )

    def owner_identity(request: Request) -> str:
        if request.headers.get("authorization"):
            return store.owner(bearer(request))
        credential = request.cookies.get(COOKIE, "")
        if not credential:
            raise RACPError("UNAUTHENTICATED", "owner authentication required")
        changing = request.method not in {"GET", "HEAD", "OPTIONS"}
        if changing and request.headers.get("origin") != str(request.base_url).rstrip("/"):
            raise RACPError("PERMISSION_DENIED", "Console mutation requires same-origin request")
        return console_auth.authenticate(
            credential,
            csrf=request.headers.get("x-csrf-token", "") if changing else None,
            touch=changing,
        )

    async def owner(request: Request) -> str:
        return owner_identity(request)

    def transfer_identity(request: Request) -> dict[str, str]:
        if not request.headers.get("authorization"):
            return {"owner": owner_identity(request)}
        credential = bearer(request)
        try:
            return {"owner": store.owner(credential)}
        except RACPError as exc:
            if exc.error.code != "UNAUTHENTICATED":
                raise
        return {"device": store.authenticate_device(credential)}

    Owner = Annotated[str, Depends(owner)]

    if oauth is not None:

        @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
        @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
        async def oauth_metadata() -> dict[str, Any]:
            return oauth.metadata()

    @app.post("/api/v1/console/setup-token")
    async def console_setup(request: Request) -> dict[str, Any]:
        return console_auth.setup(store.owner(bearer(request)))

    @app.post("/api/v1/console/session", response_model=ConsoleSession)
    async def console_login(input: ConsoleLogin, request: Request) -> Response:
        if request.headers.get("origin") != str(request.base_url).rstrip("/"):
            raise RACPError("PERMISSION_DENIED", "Console login requires same-origin request")
        secure = request.url.scheme == "https"
        if not secure and request.url.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "testserver",
        }:
            raise RACPError("PERMISSION_DENIED", "Console session requires HTTPS")
        credential, view = console_auth.exchange(input.setup_secret)
        response = JSONResponse(view.model_dump())
        response.set_cookie(
            COOKIE,
            credential,
            max_age=43200,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/api/v1/console/session", response_model=ConsoleSession)
    async def console_session(request: Request) -> ConsoleSession:
        return console_auth.view(request.cookies.get(COOKIE, ""))

    @app.post("/api/v1/console/session/activity")
    async def console_activity(principal: Owner) -> dict[str, str]:
        return {"state": "active"}

    @app.delete("/api/v1/console/session")
    async def console_logout(request: Request, principal: Owner) -> Response:
        console_auth.logout(request.cookies.get(COOKIE, ""))
        response = JSONResponse({"state": "signed_out"})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/events")
    @app.get("/api/v1/events")
    async def console_events(request: Request, principal: Owner) -> Response:
        if request.query_params:
            raise RACPError(
                "INVALID_ARGUMENT", "SSE uses Last-Event-ID; query parameters are denied"
            )
        credential = request.cookies.get(COOKIE, "")
        authorization = request.headers.get("authorization")

        def check() -> None:
            if authorization:
                store.owner(bearer(request))
            else:
                console_auth.authenticate(credential)

        stream, release = events.stream(principal, request.headers.get("last-event-id"), check)
        return EventResponse(stream, release)

    @app.get("/api/v1/doctor", response_model=DoctorView)
    async def doctor(principal: Owner) -> DoctorView:
        devices = store.devices(principal)
        return DoctorView(
            status="healthy"
            if all(
                item["info"].get("status") == "ONLINE"
                and all(
                    cap.get("healthy", False) and cap.get("unavailable_reason") is None
                    for cap in item["info"].get("capabilities", [])
                    if cap.get("enabled", True)
                )
                for item in devices
            )
            else "degraded",
            owner_id=principal,
            gateway={
                "database": "ready",
                "mcp": {
                    "authentication": "oauth" if oauth else "owner_bearer",
                    "resource": oauth.resource if oauth else None,
                    "resource_metadata": oauth.metadata_url if oauth else None,
                    "issuer": oauth.config.issuer if oauth else None,
                    "scopes": oauth.metadata()["scopes_supported"] if oauth else [],
                },
                "execution_queue": [
                    dict(row)
                    for row in store.db.execute(
                        "SELECT a.device_id,a.state,COUNT(*) AS count FROM operation_admission a "
                        "JOIN devices d ON d.id=a.device_id WHERE a.state<>'DONE' AND d.owner_id=? "
                        "GROUP BY a.device_id,a.state",
                        (principal,),
                    )
                ],
                "event_streams": sum(events.active.values()),
            },
            devices=[
                {
                    "id": item["id"],
                    "name": item["name"],
                    "status": item["info"].get("status", "OFFLINE"),
                    "execution_identity": item["info"].get("execution_identity"),
                    "last_seen_at": item["info"].get("last_seen_at"),
                    "capabilities": item["info"].get("capabilities", []),
                    "handles": control.handles.list(item["id"], principal),
                }
                for item in devices
            ],
            limitations=[
                "Single Gateway worker",
                "Windows Service/Broker and reference OS verification pending",
                "Missing operations remain unavailable",
            ],
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready(principal: Owner) -> dict[str, Any]:
        store.db.execute("SELECT 1")
        return {"status": "ready", "protocol": 1, "owner_initialized": True}

    @app.post("/api/v1/enrollment-tokens")
    async def enrollment(
        input: EnrollmentInput, request: Request, principal: Owner
    ) -> dict[str, Any]:
        origin = public_origin or str(request.base_url).rstrip("/")
        if input.include_connection_file:
            try:
                ConnectionFile(
                    gateway=origin,
                    token="x" * 20,
                    expires_at=datetime.now(UTC) + timedelta(seconds=600),
                    ca_pem=client_ca_pem,
                )
            except ValueError:
                raise RACPError(
                    "INVALID_ARGUMENT", "Connection file requires a valid Gateway origin"
                ) from None
        secret = store.enrollment(input.name)
        result: dict[str, Any] = {"token": secret, "expires_in_seconds": 600}
        if input.include_connection_file:
            result["connection_file"] = ConnectionFile(
                gateway=origin,
                token=secret,
                expires_at=datetime.now(UTC) + timedelta(seconds=600),
                ca_pem=client_ca_pem,
            ).model_dump(mode="json")
        return result

    @app.post("/agent/v1/enroll")
    async def enroll(input: EnrollInput) -> dict[str, str]:
        return store.enroll(input.token)

    @app.get("/api/v1/devices", response_model=ListPage[DeviceView])
    async def devices(
        principal: Owner,
        state: Annotated[
            str | None, Field(pattern=r"^(ONLINE|OFFLINE|DEGRADED|CONNECTING|REVOKED)$")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        cursor: Annotated[str | None, Field(max_length=2048)] = None,
    ) -> dict[str, Any]:
        return lists.page("devices", principal, limit=limit, cursor=cursor, state=state)

    @app.get("/api/v1/devices/{device_id}")
    async def device(device_id: str, principal: Owner) -> dict[str, Any]:
        return store.device(device_id, principal)

    @app.post("/api/v1/devices/{device_id}/revoke")
    async def revoke(device_id: str, principal: Owner) -> dict[str, str]:
        await control.revoke(device_id, principal)
        return {"state": "REVOKED"}

    @app.get("/api/v1/devices/{device_id}/handles")
    async def device_handles(device_id: str, principal: Owner) -> dict[str, Any]:
        return {"items": control.handles.list(device_id, principal), "next_cursor": None}

    @app.get("/api/v1/handles/{handle_id}")
    async def resource_handle(handle_id: str, principal: Owner) -> dict[str, Any]:
        return control.handles.get(handle_id, principal)

    @app.post("/api/v1/devices/{device_id}/credentials/rotate")
    async def rotate(device_id: str, principal: Owner) -> dict[str, str]:
        store.device(device_id, principal)
        return {"credential": store.rotate(device_id)}

    @app.post("/api/v1/operations")
    async def execute(input: OperationInput, principal: Owner) -> Response:
        value = await control.execute(input, principal)
        return JSONResponse(value, status_code=202 if input.execution_mode == "job" else 200)

    @app.get("/api/v1/operations/{operation_id}")
    async def operation(operation_id: str, principal: Owner) -> dict[str, Any]:
        return control.get(operation_id, principal)

    @app.post("/api/v1/operations/{operation_id}/cancel")
    async def cancel(operation_id: str, principal: Owner) -> dict[str, Any]:
        return await control.cancel(operation_id, principal)

    @app.get("/api/v1/operations/{operation_id}/resolutions")
    async def resolutions(operation_id: str, principal: Owner) -> dict[str, Any]:
        return control.resolutions(operation_id, principal)

    @app.get("/api/v1/operations/{operation_id}/outputs")
    async def operation_outputs(operation_id: str, principal: Owner) -> dict[str, Any]:
        return control.outputs(operation_id, principal)

    @app.get("/api/v1/jobs/{job_id}")
    async def job(job_id: str, principal: Owner) -> dict[str, Any]:
        return control.jobs.get(job_id, principal)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, principal: Owner) -> dict[str, Any]:
        return await control.cancel_job(job_id, principal)

    @app.get("/api/v1/jobs", response_model=ListPage[JobView])
    async def jobs(
        principal: Owner,
        device_id: str | None = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        cursor: Annotated[str | None, Field(max_length=2048)] = None,
        state: JobState | None = None,
    ) -> ListPage[JobView]:
        return ListPage[JobView].model_validate_json(
            json.dumps(
                control.jobs.list(
                    principal, device=device_id, limit=limit, cursor=cursor, state=state
                )
            )
        )

    @app.get("/api/v1/approvals", response_model=ListPage[ApprovalView])
    async def approvals(
        principal: Owner,
        device_id: str | None = None,
        state: Annotated[
            str | None, Field(pattern=r"^(PENDING|APPROVED|DENIED|EXPIRED|CONSUMED)$")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        cursor: Annotated[str | None, Field(max_length=2048)] = None,
    ) -> dict[str, Any]:
        def convert(row: dict[str, Any]) -> dict[str, Any]:
            record = store.approval_record(row["id"])
            operation = store.get(record["operation_id"])
            request = operation["request"]
            device = store.device(operation["device_id"], principal)
            return {
                **record,
                "device_id": device["id"],
                "device_name": device["name"],
                "execution_identity": device["info"].get("execution_identity"),
                "agent_boot_id": device["info"].get("agent_boot_id"),
                "operation": request["operation"],
                "profile": request["context"]["execution_profile_id"],
                "target": {
                    "workspace_id": request["context"].get("workspace_id", "default"),
                    **{
                        key: value
                        for key, value in request.get("payload", {}).items()
                        if key
                        in {
                            "argv",
                            "mode",
                            "shell",
                            "command",
                            "cwd",
                            "path",
                            "source",
                            "destination",
                            "destination_workspace_id",
                            "pid",
                            "handle_id",
                        }
                    },
                },
            }

        return lists.page(
            "approvals",
            principal,
            limit=limit,
            cursor=cursor,
            state=state,
            device_id=device_id,
            convert=convert,
        )

    @app.get("/api/v1/approvals/{approval_id}")
    async def approval(approval_id: str, principal: Owner) -> dict[str, Any]:
        record = store.approval_record(approval_id)
        control.get(record["operation_id"], principal)
        return record

    @app.post("/api/v1/approvals/{approval_id}/approve")
    async def approve(approval_id: str, principal: Owner) -> dict[str, Any]:
        await approval(approval_id, principal)
        return store.decide_approval(approval_id, True)

    @app.post("/api/v1/approvals/{approval_id}/deny")
    async def deny(approval_id: str, principal: Owner) -> dict[str, Any]:
        await approval(approval_id, principal)
        return store.decide_approval(approval_id, False)

    @app.post("/api/v1/approvals/{approval_id}/execute")
    async def execute_approved(approval_id: str, principal: Owner) -> Response:
        record = await approval(approval_id, principal)
        request = store.get(record["operation_id"])["request"]
        input = OperationInput(
            device_id=request["device_id"],
            workspace_id=request["context"].get("workspace_id", "default"),
            operation=request["operation"],
            payload=request["payload"],
            timeout_ms=request["timeout_ms"],
            execution_mode=request["execution_mode"],
            idempotency_key=request["idempotency_key"],
            execution_profile_id=request["context"]["execution_profile_id"],
            approval_id=approval_id,
        )
        result = await control.execute(input, principal)
        return JSONResponse(result, status_code=202 if input.execution_mode == "job" else 200)

    @app.get("/api/v1/audit", response_model=ListPage[AuditView])
    async def audit(
        principal: Owner,
        device_id: str | None = None,
        event: Annotated[str | None, Field(max_length=128, pattern=r"^[a-z_]+$")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        cursor: Annotated[str | None, Field(max_length=2048)] = None,
    ) -> dict[str, Any]:
        return lists.page(
            "audit", principal, limit=limit, cursor=cursor, state=event, device_id=device_id
        )

    @app.post("/agent/v1/operations/{operation_id}/output")
    async def upload_output(operation_id: str, request: Request) -> dict[str, Any]:
        device_id = store.authenticate_device(bearer(request))
        return await artifacts.upload(
            request, operation_id, device_id, request.headers.get("x-content-sha256", "")
        )

    @app.get("/api/v1/artifacts/{artifact_id}")
    async def artifact(artifact_id: str, principal: Owner) -> dict[str, Any]:
        return artifacts.get(artifact_id, principal)

    @app.get("/api/v1/artifacts/{artifact_id}/content")
    async def content(artifact_id: str, request: Request, principal: Owner) -> Response:
        return artifacts.response(artifact_id, principal, request)

    @app.get("/api/v1/artifacts", response_model=ListPage[ArtifactView])
    async def artifact_list(
        principal: Owner,
        device_id: str | None = None,
        state: Annotated[
            str | None, Field(pattern=r"^(READY|UPLOADING|VERIFYING|FAILED|EXPIRED|DELETED)$")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        cursor: Annotated[str | None, Field(max_length=2048)] = None,
    ) -> dict[str, Any]:
        return lists.page(
            "artifacts",
            principal,
            limit=limit,
            cursor=cursor,
            state=state,
            device_id=device_id,
            convert=lambda row: artifacts.get(row["id"], principal),
        )

    @app.post("/api/v1/artifact-transfers")
    async def transfer_create(input: TransferCreate, request: Request) -> dict[str, Any]:
        return artifacts.create(input, **transfer_identity(request))

    @app.get("/api/v1/artifact-transfers/{transfer_id}")
    async def transfer_get(transfer_id: str, request: Request) -> dict[str, Any]:
        try:
            identity = transfer_identity(request)
        except RACPError as exc:
            if exc.error.code != "UNAUTHENTICATED":
                raise
            artifacts.scoped(transfer_id, bearer(request))
        else:
            artifacts.authorize_identity(transfer_id, **identity)
        return artifacts.status(transfer_id)

    @app.post("/api/v1/artifact-transfers/{transfer_id}/authorize")
    async def transfer_authorize(transfer_id: str, request: Request) -> dict[str, Any]:
        return artifacts.renew(transfer_id, **transfer_identity(request))

    @app.put("/api/v1/artifact-transfers/{transfer_id}/content")
    async def transfer_put(transfer_id: str, request: Request) -> dict[str, Any]:
        return await artifacts.put(transfer_id, bearer(request), request)

    @app.post("/api/v1/artifact-transfers/{transfer_id}/complete")
    async def transfer_complete(
        transfer_id: str, input: TransferComplete, request: Request
    ) -> dict[str, Any]:
        return await artifacts.complete(transfer_id, bearer(request), input)

    @app.get("/api/v1/artifact-transfers/{transfer_id}/content")
    async def transfer_download(transfer_id: str, request: Request) -> Response:
        credential = bearer(request)
        record = artifacts.scoped(transfer_id, credential, "download")
        return artifacts.response(
            record["artifact_id"],
            record["owner_id"],
            request,
            authorize=lambda: artifacts.scoped(transfer_id, credential, "download"),
        )

    @app.post("/api/v1/artifacts/gc")
    async def artifact_gc(principal: Owner) -> dict[str, int]:
        return await artifacts.collect()

    @app.websocket("/agent/v1/connect")
    async def connect(socket: WebSocket) -> None:
        if public_origin and not socket_origin_matches(socket):
            await socket.close(code=1008)
            return
        connection: Connection | None = None
        device_id = ""
        try:
            if socket.headers.get("origin"):
                raise RACPError("PERMISSION_DENIED", "browser agent connections denied")
            device_id = store.authenticate_device(bearer(socket))
            await socket.accept()
            message = decode_message(await asyncio.wait_for(socket.receive_text(), 10))
            if not isinstance(message, Hello) or message.device_id != device_id:
                raise RACPError("INVALID_ARGUMENT", "expected authenticated hello")
            if 1 not in message.supported_protocols:
                raise RACPError("PROTOCOL_MISMATCH", "no common protocol")
            epoch = store.connected(device_id, {**message.model_dump(), "status": "CONNECTING"})
            previous = control.connections.get(device_id)
            if previous:
                await previous.socket.close(code=4009, reason="STALE_CONNECTION")
            connection = Connection(
                socket,
                message.agent_boot_id,
                epoch,
                {
                    op
                    for cap in message.capabilities
                    if cap.enabled and cap.healthy
                    for op in cap.operations
                },
            )
            control.connections[device_id] = connection
            await connection.send(
                Welcome(
                    device_id=device_id, agent_boot_id=connection.boot_id, connection_epoch=epoch
                )
            )
            while True:
                reconcile = decode_message(await asyncio.wait_for(socket.receive_text(), 10))
                if (
                    not isinstance(reconcile, Reconcile)
                    or reconcile.connection_epoch != epoch
                    or reconcile.device_id != device_id
                    or reconcile.agent_boot_id != connection.boot_id
                ):
                    raise RACPError("INVALID_ARGUMENT", "expected authenticated reconciliation")
                for reconciled in reconcile.records:
                    control.accept_result(reconciled, connection, reconcile=True)
                for expired in reconcile.expired:
                    control.accept_expired(expired, connection)
                control.handles.observe(
                    reconcile.handles, device_id, connection.boot_id, complete=reconcile.complete
                )
                if reconcile.complete:
                    control.reconcile_active(reconcile.active, device_id, connection)
                    break
            connection.ready = True
            control.scheduler.wake.set()
            store.device_status(device_id, "ONLINE")
            await connection.send(
                Heartbeat(
                    device_id=device_id, agent_boot_id=connection.boot_id, connection_epoch=epoch
                )
            )
            while True:
                raw = await asyncio.wait_for(socket.receive_text(), 15)
                try:
                    incoming = decode_message(raw)
                except (ValueError, ValidationError):
                    # Malformed frames cannot dispatch a provider or terminate the core Agent.
                    await socket.close(code=1008, reason="invalid frame")
                    break
                if (
                    incoming.device_id != device_id
                    or incoming.agent_boot_id != connection.boot_id
                    or getattr(incoming, "connection_epoch", None) != epoch
                    or control.connections.get(device_id) is not connection
                ):
                    raise RACPError("STALE_CONNECTION", "connection fenced")
                if isinstance(incoming, Heartbeat):
                    store.authenticate_device(bearer(socket))
                    connection.last_heartbeat = asyncio.get_running_loop().time()
                    store.device_heartbeat(device_id, incoming.health)
                    if incoming.capabilities is not None:
                        names = {cap.name for cap in incoming.capabilities}
                        if (
                            len(names) != len(incoming.capabilities)
                            or any(
                                name not in {spec.capability for spec in REGISTRY.values()}
                                for name in names
                            )
                            or any(
                                op not in REGISTRY or REGISTRY[op].capability != cap.name
                                for cap in incoming.capabilities
                                for op in cap.operations
                            )
                        ):
                            raise RACPError("PERMISSION_DENIED", "capability update scope mismatch")
                        info = store.device(device_id)["info"]
                        merged = [
                            cap
                            for cap in info.get("capabilities", [])
                            if cap.get("name") not in names
                        ]
                        merged.extend(cap.model_dump() for cap in incoming.capabilities)
                        if merged != info.get("capabilities", []):
                            with store.transaction():
                                store.db.execute(
                                    "UPDATE devices SET info="
                                    "json_set(info,'$.capabilities',json(?)) WHERE id=?",
                                    (json.dumps(merged), device_id),
                                )
                                store.audit(
                                    "device_capabilities_changed",
                                    {"device_id": device_id},
                                    names=sorted(names),
                                )
                        connection.capabilities = {
                            op
                            for cap in merged
                            if cap.get("enabled", True) and cap.get("healthy", False)
                            for op in cap.get("operations", [])
                        }
                    if incoming.handles is not None:
                        control.handles.observe(
                            incoming.handles, device_id, connection.boot_id, complete=True
                        )
                    await connection.send(incoming)
                elif isinstance(incoming, Ack):
                    record = store.get(incoming.operation_id)
                    if (
                        record["device_id"] != device_id
                        or record["request"]["request_id"] != incoming.request_id
                    ):
                        raise RACPError("STALE_CONNECTION", "ack correlation mismatch")
                    if record["state"] == "DISPATCHED":
                        store.transition(incoming.operation_id, "RUNNING")
                elif isinstance(incoming, BrowserState):
                    accepted_state = control.browser_events.accept(incoming)
                    if accepted_state and incoming.capability is not None:
                        connection.capabilities = {
                            op for op in connection.capabilities if not op.startswith("browser.")
                        }
                        if incoming.capability.enabled and incoming.capability.healthy:
                            connection.capabilities.update(incoming.capability.operations)
                    await connection.send(
                        BrowserStateAck(
                            device_id=device_id,
                            agent_boot_id=connection.boot_id,
                            connection_epoch=epoch,
                            provider_instance_id=incoming.provider_instance_id,
                            event_sequence=incoming.event_sequence,
                        )
                    )
                elif isinstance(incoming, Result):
                    control.accept_result(incoming, connection)
                elif isinstance(incoming, OutcomeExpired):
                    control.accept_expired(incoming, connection)
                elif isinstance(incoming, JobUpdate):
                    control.job_update(incoming, connection)
                elif isinstance(incoming, (StreamOpened, StreamData, StreamGap, StreamEnd)):
                    control.streams.feed(incoming, connection)
                else:
                    raise RACPError("INVALID_ARGUMENT", "message not allowed in current state")
        except (WebSocketDisconnect, TimeoutError, ValueError, RACPError, OSError):
            try:
                await socket.close(code=1008)
            except (RuntimeError, WebSocketDisconnect, OSError):
                pass
        finally:
            if connection:
                control.streams.disconnected(device_id, connection)
                await connection.writer.close()
            if connection and control.connections.get(device_id) is connection:
                del control.connections[device_id]
                control.handles.offline(device_id)
                if not store.device(device_id)["revoked"]:
                    store.device_status(device_id, "OFFLINE")
                for record in store.iter_records(device_id, nonterminal=True):
                    if record["state"] not in TERMINAL_STATES and record["state"] != "ACCEPTED":
                        store.transition(record["id"], "RECONCILING")

    @app.websocket("/api/v1/devices/{device_id}/terminals/{handle_id}/stream")
    async def terminal_output(socket: WebSocket, device_id: str, handle_id: str) -> None:
        if public_origin and not socket_origin_matches(socket):
            await socket.close(code=1008)
            return
        try:
            if socket.query_params:
                raise RACPError(
                    "PERMISSION_DENIED", "stream credentials cannot use query parameters"
                )
            origin = socket.headers.get("origin")
            expected = str(
                socket.url.replace(
                    scheme="http" if socket.url.scheme == "ws" else "https", path="", query=""
                )
            ).rstrip("/")
            if origin and origin != expected:
                raise RACPError("PERMISSION_DENIED", "stream origin denied")
            authorization = socket.headers.get("authorization")
            cookie = socket.cookies.get(COOKIE, "")
            if authorization:
                principal = store.owner(bearer(socket))
            else:
                if not cookie:
                    raise RACPError("UNAUTHENTICATED", "owner authentication required")
                if origin != expected:
                    raise RACPError("PERMISSION_DENIED", "Console stream requires same origin")
                principal = console_auth.authenticate(cookie)

            def recheck() -> None:
                if authorization:
                    store.owner(bearer(socket))
                else:
                    console_auth.authenticate(cookie)
        except RACPError as exc:
            await socket.send_denial_response(
                JSONResponse(
                    {"error": asdict(exc.error)}, status_code=HTTP_ERRORS.get(exc.error.code, 403)
                )
            )
            return
        await terminal_socket(socket, device_id, handle_id, principal, control, recheck)

    app.mount("/mcp", mcp_app)
    console_build = console_dir or Path(__file__).with_name("static") / "console"
    if not console_build.is_dir():
        console_build = Path(__file__).parents[4] / "apps" / "console" / "dist"
    if console_build.is_dir():
        app.mount("/console", StaticFiles(directory=console_build, html=True), name="console")
    return app
