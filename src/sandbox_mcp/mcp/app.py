import contextlib
import hmac

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from sandbox_mcp.db.models import Base
from sandbox_mcp.db.repo import Repository
from sandbox_mcp.db.session import make_engine, make_session_factory
from sandbox_mcp.mcp.server import build_mcp
from sandbox_mcp.settings import Settings


class GatewayTokenMiddleware(BaseHTTPMiddleware):
    """Only LiteLLM (holding SANDBOX_GATEWAY_TOKEN) may talk to the MCP endpoint."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        ok = auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], self._token)
        if not ok:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app(settings: Settings) -> Starlette:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    repo = Repository(make_session_factory(engine))
    mcp = build_mcp(repo, settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            yield

    async def healthz(_: Request):
        return JSONResponse({"status": "ok"})

    # The service is reachable only through LiteLLM inside the compose network and is
    # protected by the gateway token, so Host-header (DNS rebinding) checks are disabled.
    mcp_app = mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return Starlette(
        routes=[Route("/healthz", healthz), Mount("/", app=mcp_app)],
        middleware=[Middleware(GatewayTokenMiddleware, token=settings.gateway_token)],
        lifespan=lifespan,
    )
