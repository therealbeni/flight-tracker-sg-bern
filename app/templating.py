import time

from fastapi.templating import Jinja2Templates

import timeutil
from paths import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Cache-busts /static/* URLs on every deploy (see base.html), so Cloudflare's edge
# cache and browsers pick up new CSS/JS immediately instead of serving a stale
# cached copy for however long its cache-control max-age says.
templates.env.globals["asset_version"] = str(int(time.time()))



def current_user(request):
    """The logged-in account, if the page looked it up (deps.get_current_pilot)."""
    return getattr(request.state, "user", None)


templates.env.globals["current_user"] = current_user

# Times are stored in UTC and always shown in Swiss local time: {{ f.takeoff_time|hhmm }}
templates.env.filters["hhmm"] = timeutil.fmt_time
templates.env.filters["datum"] = timeutil.fmt_date
templates.env.filters["datum_zeit"] = timeutil.fmt_datetime
templates.env.filters["flugzeit"] = timeutil.fmt_duration
templates.env.filters["wochentag"] = timeutil.fmt_weekday
