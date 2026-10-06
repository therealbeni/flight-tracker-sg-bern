from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from deps import get_current_pilot
from models import Pilot
from paths import STATIC_DIR
from routers import account, admin, auth, claim, dashboard, flights, flugbuch, logbook, statistik
from templating import templates

# No automatic API docs (/docs, /openapi.json): they'd list every endpoint and
# parameter to anyone, and nobody uses them - the app is HTML pages only.
app = FastAPI(title="Flight Tracker SG Bern", docs_url=None, redoc_url=None, openapi_url=None)

# Only our own scripts and styles run on our pages: an injected <script> or
# onclick="" is ignored by the browser. 'inline-speculation-rules' allows the
# prefetch hints in base.html, which are data, not code.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'", "script-src 'self' 'inline-speculation-rules'", "style-src 'self'",
    "img-src 'self' data:", "media-src 'self' blob:", "connect-src 'self'", "object-src 'none'",
    "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
])


# Registered before SessionMiddleware, so it runs inside it (Starlette wraps
# later middleware around earlier ones) and the error page can use the session.
@app.middleware("http")
async def protect(request: Request, call_next):
    # Forms posted from another website (cross-site request forgery). The
    # session cookie is SameSite=lax already, which stops most of it; checking
    # the Origin the browser sends is a second, independent line of defence.
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != "null" and urlsplit(origin).netloc != request.headers.get("host"):
            return templates.TemplateResponse(request, "error.html", {
                "status_code": 403, "detail": "Dieses Formular kam von einer fremden Webseite."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")  # no embedding in other sites (clickjacking)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    # The QR scanner needs the camera; nothing needs location or microphone.
    response.headers.setdefault("Permissions-Policy", "camera=(self), geolocation=(), microphone=(), payment=()")
    if settings.base_url.startswith("https://"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")  # HTTPS only from now on
    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    # TLS is terminated at the Cloudflare edge in production (BASE_URL is https://...);
    # the browser's connection is HTTPS even though app<->Caddy<->cloudflared is plain
    # HTTP inside the private Docker network, so the Secure cookie flag is correct here.
    https_only=settings.base_url.startswith("https://"),
)
class CachedStaticFiles(StaticFiles):
    """CSS, JS and images are linked with ?v=<deploy> (templating.asset_version),
    so a browser may keep them for a year without asking again - a new deploy
    links new URLs. Saves a round trip per file on every page at the field."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if b"v=" in scope.get("query_string", b"") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")



app.include_router(account.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(claim.router)
app.include_router(dashboard.router)
app.include_router(flights.router)
app.include_router(flugbuch.router)
app.include_router(logbook.router)
app.include_router(statistik.router)


@app.exception_handler(HTTPException)
async def redirect_aware_exception_handler(request: Request, exc: HTTPException):
    # require_login raises a 303 with a Location header to send unauthenticated
    # browser requests to the login page instead of showing a raw error page.
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return templates.TemplateResponse(
        request, "error.html", {"status_code": exc.status_code, "detail": exc.detail}, status_code=exc.status_code
    )


@app.exception_handler(RequestValidationError)
async def friendly_validation_error(request: Request, exc: RequestValidationError):
    # A mangled link or form (e.g. /flights/abc, a date that isn't one) -
    # show a page in German instead of FastAPI's JSON.
    return templates.TemplateResponse(request, "error.html", {
        "status_code": 400, "detail": "Da stimmt etwas mit dem Link oder den Eingaben nicht. Bitte versuche es nochmals."
    }, status_code=400)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    # Runs outside the session middleware, so no templates (they read the
    # session) - just a plain page. The traceback still goes to the log.
    import traceback
    traceback.print_exception(exc)
    return HTMLResponse(
        '<!DOCTYPE html><html lang="de-CH"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Fehler - SG Bern</title><link rel="stylesheet" href="/static/style.css"><body><main class="content">'
        "<h1>Da ist etwas schiefgelaufen</h1><p>Bitte versuche es nochmals. Falls es wieder passiert, "
        'melde dich beim Vorstand.</p><p><a href="/dashboard">Zur Startseite</a></p></main></body></html>',
        status_code=500,
    )


@app.get("/hilfe")
def help_page(request: Request, user: Pilot | None = Depends(get_current_pilot)):
    return templates.TemplateResponse(request, "hilfe.html", {"pilot": user})


@app.get("/")
def root():
    return RedirectResponse("/dashboard", status_code=303)
