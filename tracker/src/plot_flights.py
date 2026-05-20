import time
import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt

_last_plot_time = 0.0
_fig = None
_ax = None


def plot_active_flights(active_flights: dict, interval_s: float = 5.0) -> None:
    global _last_plot_time, _fig, _ax

    now = time.monotonic()
    if now - _last_plot_time < interval_s:
        return
    _last_plot_time = now

    if _fig is None:
        plt.ion()
        _fig, _ax = plt.subplots()
        plt.show(block=False)

    records = [r for r in active_flights.values() if r.latitude is not None and r.longitude is not None]

    _ax.clear()
    if records:
        lons = np.array([r.longitude for r in records])
        lats = np.array([r.latitude for r in records])
        _ax.scatter(lons, lats, s=20, zorder=5)

    _ax.set_xlabel("Longitude")
    _ax.set_ylabel("Latitude")
    _ax.set_title(f"Active flights: {len(records)}")
    _fig.canvas.draw()
    _fig.canvas.flush_events()
