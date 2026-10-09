"""Weather watcher (public, no bet data). Open-Meteo (3 models) + NWS alerts per game window. Returns alerts on level increase."""
import json, os, datetime as dt, urllib.request
UA = {"User-Agent": "game-watch (github.com/inline421/game-watch)", "Accept": "application/json"}
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]
SEV = ("Tornado", "Severe Thunderstorm", "Flood", "High Wind", "Hurricane", "Tropical", "Lightning", "Extreme Wind", "Blizzard", "Winter Storm", "Ice Storm")
def _g(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25) as r: return json.loads(r.read().decode())
def level(g, now):
    kick = dt.datetime.strptime(g["kick"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
    h = (kick - now).total_seconds() / 3600
    if h < -3.5 or h > 36: return None
    d = _g("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=wind_gusts_10m,precipitation,weather_code&wind_speed_unit=mph&timezone=UTC&forecast_days=4&models=%s" % (g["lat"], g["lon"], ",".join(MODELS)))["hourly"]
    ts = [dt.datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc) for t in d["time"]]
    idx = [i for i, t in enumerate(ts) if kick - dt.timedelta(hours=1) <= t <= kick + dt.timedelta(hours=3.5)]
    hits = 0; notes = []
    for m in MODELS:
        gu = [d["wind_gusts_10m_" + m][i] or 0 for i in idx]; pr = sum((d["precipitation_" + m][i] or 0) for i in idx); ts_ = any((d["weather_code_" + m][i] or 0) >= 95 for i in idx)
        mg = max(gu) if gu else 0
        if mg >= 30 or pr >= 10 or ts_: hits += 2
        elif mg >= 22 or pr >= 3: hits += 1
        notes.append("%s gust %.0f mph, precip %.1f mm%s" % (m.split("_")[0], mg, pr, ", T-STORM" if ts_ else ""))
    lv = 2 if hits >= 4 else 1 if hits >= 2 else 0
    try:
        for f in _g("https://api.weather.gov/alerts/active?point=%s,%s" % (g["lat"], g["lon"])).get("features", []):
            ev = f["properties"].get("event", "")
            if any(s in ev for s in SEV): lv = max(lv, 2); notes.append("NWS: " + ev)
    except Exception as e: pass
    return lv, notes, h
def run(games, st, now):
    out = []; ws = st.setdefault("_wx", {})
    for g in games:
        try: r = level(g, now)
        except Exception as e: print("wx fail", g["id"], str(e)[:60]); continue
        if r is None: continue
        lv, notes, h = r; prev = ws.get(g["id"], 0)
        if lv != prev:
            out.append(("WEATHER %s level %d (kick in %.1fh)" % (g["id"], lv, h), notes, prev, lv))
        ws[g["id"]] = lv
    return out
