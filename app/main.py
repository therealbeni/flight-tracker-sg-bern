from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from paths import STATIC_DIR
from routers import admin, auth, claim, dashboard, flights, flugbuch, logbook
from templating import templates

app = FastAPI(title="Flight Tracker SG Bern")
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


@app.get("/")
def root():
    return RedirectResponse("/dashboard", status_code=303)
