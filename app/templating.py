import time

from fastapi.templating import Jinja2Templates

from paths import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Cache-busts /static/* URLs on every deploy (see base.html), so Cloudflare's edge
# cache and browsers pick up new CSS/JS immediately instead of serving a stale
# cached copy for however long its cache-control max-age says.
templates.env.globals["asset_version"] = str(int(time.time()))
