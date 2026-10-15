"""Daily weather board (public data only), run inside game-watch where the network is open.
Builds the slate from ESPN (NFL + FBS, next 3 days), geocodes each outdoor venue (Open-Meteo geocoding),
pulls Open-Meteo 3-model hourly forecast for kick-1h..kick+3.5h and NWS active alerts.
Writes wx_latest.json for cloud tasks (they can't reach weather sites). Refreshed every 30 min by watch.py."""
import json, os, datetime as dt, urllib.request, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "wx_latest.json")
CACHE = os.path.join(HERE, "venue_geo.json")
UA = {"User-Agent": "game-watch (github.com/inline421/game-watch)", "Accept": "application/json"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/%s/scoreboard?dates=%s&limit=400%s"
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]
SEV = ("Tornado", "Severe Thunderstorm", "Flood", "High Wind", "Hurricane", "Tropical", "Lightning", "Extreme Wind", "Blizzard", "Winter Storm", "Ice Storm")

def g(u, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            import time; time.sleep(2 + 3 * i)
    raise last

def geo(city, state, cache):
    k = "%s, %s" % (city, state)
    if k in cache: return cache[k]
    r = g("https://geocoding-api.open-meteo.com/v1/search?count=5&country=US&name=" + urllib.parse.quote(city)).get("results") or []
    pick = next((x for x in r if state and (x.get("admin1_code") == state or x.get("admin1", "").lower().startswith(state.lower()))), r[0] if r else None)
    cache[k] = [pick["latitude"], pick["longitude"]] if pick else None
    return cache[k]

def window(lat, lon, kick):
    d = g("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=wind_speed_10m,wind_gusts_10m,precipitation,temperature_2m,weather_code&wind_speed_unit=mph&precipitation_unit=inch&temperature_unit=fahrenheit&timezone=UTC&forecast_days=5&models=%s" % (lat, lon, ",".join(MODELS)))["hourly"]
    ts = [dt.datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc) for t in d["time"]]
    idx = [i for i, t in enumerate(ts) if kick - dt.timedelta(hours=1) <= t <= kick + dt.timedelta(hours=3.5)]
    per = {}
    for m in MODELS:
        def col(k): return [d.get(k + "_" + m, [None] * len(ts))[i] for i in idx]
        w = [x for x in col("wind_speed_10m") if x is not None]; gu = [x for x in col("wind_gusts_10m") if x is not None]
        pr = [x for x in col("precipitation") if x is not None]; te = [x for x in col("temperature_2m") if x is not None]
        per[m.split("_")[0]] = {"wind": round(sum(w) / len(w), 1) if w else None, "gust_max": max(gu) if gu else None,
                               "rain_in": round(sum(pr), 2) if pr else None, "temp": round(sum(te) / len(te)) if te else None,
                               "tstorm": any((x or 0) >= 95 for x in col("weather_code"))}
    # betting-relevant: sustained wind drives totals more than one-hour gust spikes; need 2 of 3 models to agree
    major = sum(1 for v in per.values() if (v["wind"] or 0) >= 20 or (v["rain_in"] or 0) >= 0.3 or v["tstorm"])
    notable = sum(1 for v in per.values() if (v["wind"] or 0) >= 15 or (v["gust_max"] or 0) >= 35 or (v["rain_in"] or 0) >= 0.1)
    return per, (2 if major >= 2 else 1 if notable >= 2 else 0)

def run(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    games = []
    for lg, extra in (("nfl", ""), ("college-football", "&groups=80")):
        for k in range(4):
            day = (now - dt.timedelta(hours=8) + dt.timedelta(days=k)).strftime("%Y%m%d")
            try: evs = g(ESPN % (lg, day, extra)).get("events", [])
            except Exception as e: print("wxdaily sb fail", lg, day, str(e)[:60]); continue
            for ev in evs:
                c = ev["competitions"][0]; ven = c.get("venue", {}); addr = ven.get("address", {})
                ab = {x["homeAway"]: x["team"]["abbreviation"] for x in c["competitors"]}
                kick = dt.datetime.strptime(ev["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
                if kick < now - dt.timedelta(hours=4) or kick > now + dt.timedelta(days=3.5): continue
                row = {"game": "%s@%s" % (ab.get("away"), ab.get("home")), "league": lg, "kick": ev["date"],
                       "venue": ven.get("fullName", ""), "city": addr.get("city", ""), "state": addr.get("state", ""), "indoor": bool(ven.get("indoor"))}
                if row["indoor"]: row["level"] = 0; row["note"] = "indoor/dome"; games.append(row); continue
                try:
                    ll = geo(row["city"], row["state"], cache)
                    if not ll: row["err"] = "geocode failed"; games.append(row); continue
                    row["models"], row["level"] = window(ll[0], ll[1], kick)
                    try:
                        al = [f["properties"].get("event", "") for f in g("https://api.weather.gov/alerts/active?point=%s,%s" % (ll[0], ll[1])).get("features", [])]
                        sev = [a for a in al if any(s in a for s in SEV)]
                        if sev: row["nws_alerts"] = sev; row["level"] = max(row["level"], 2)
                    except Exception: pass
                except Exception as e:
                    row["err"] = str(e)[:80]
                games.append(row)
    json.dump(cache, open(CACHE, "w"))
    seen = set(); uniq = []
    for r in games:
        if r["game"] + r["kick"] in seen: continue
        seen.add(r["game"] + r["kick"]); uniq.append(r)
    uniq.sort(key=lambda r: (-(r.get("level") or 0), r["kick"]))
    json.dump({"generated": now.strftime("%Y-%m-%dT%H:%MZ"), "levels": "0 calm; 1 notable = 2+ models: sustained wind >=15 mph or gust >=35 or rain >=0.1in; 2 major = 2+ models: sustained wind >=20 or rain >=0.3in or t-storm, or NWS severe alert", "games": uniq}, open(OUT, "w"), separators=(",", ":"))
    return len(uniq)

if __name__ == "__main__":
    print("games", run())
