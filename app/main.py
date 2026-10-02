from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from deps import get_current_pilot
from models import Pilot
from paths import STATIC_DIR
from routers import admin, auth, claim, dashboard, flights, flugbuch, logbook
from templating import templates

app = FastAPI(title="Flight Tracker SG Bern")


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
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")



app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(claim.router)
app.include_router(dashboard.router)
app.include_router(flights.router)
app.include_router(flugbuch.router)
app.include_router(logbook.router)


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
        '<title>Fehler - SG Bern</title><body style="font-family:sans-serif;max-width:30rem;margin:3rem auto;padding:1rem">'
        "<h1>Da ist etwas schiefgelaufen</h1><p>Bitte versuche es nochmals. Falls es wieder passiert, "
        'melde dich beim Vorstand.</p><p><a href="/dashboard">Zur Startseite</a></p></body></html>',
        status_code=500,
    )


@app.get("/hilfe")
def help_page(request: Request, user: Pilot | None = Depends(get_current_pilot)):
    return templates.TemplateResponse(request, "hilfe.html", {"pilot": user})


@app.get("/")
def root():
    return RedirectResponse("/dashboard", status_code=303)
