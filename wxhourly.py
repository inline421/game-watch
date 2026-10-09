"""Hourly forecast per venue (public data only): Open-Meteo 3-model mean + NWS gridpoint. Writes wxhourly.json."""
import json, re, datetime as dt, urllib.request
UA = {"User-Agent": "game-watch (github.com/inline421/game-watch)", "Accept": "application/json"}
V = {"FSU@LOU": (38.2062, -85.7585), "ODU@APP": (36.2112, -81.685), "SC@FLA": (29.65, -82.35), "MISS@VAN": (36.1445, -86.8095),
"DUKE@GT": (33.7724, -84.3928), "UGA@BAMA": (33.2084, -87.5504), "UAB@MEM": (35.1209, -89.9764), "LSU@UK": (38.0222, -84.5058),
"UL@LT": (32.5276, -92.6428), "JMU@GASO": (32.4217, -81.7773), "WAKE@NCSU": (35.8007, -78.7197), "RICE@ECU": (35.5964, -77.3687),
"TENN@ARK": (36.0678, -94.1789), "FAMU@ALST": (32.3636, -86.2951), "SAM@UTC": (35.0476, -85.3066), "WCU@MER": (32.8296, -83.6488),
"TAR@APSU": (36.5315, -87.351), "BCU@AAMU": (34.786, -86.571), "CIN@MIA": (25.958, -80.239), "NYG@WAS": (38.9078, -76.8645),
"HOU@TEN": (36.1665, -86.7713), "IND@PIT": (40.4468, -80.0158), "CLE@NYJ": (40.8135, -74.0745), "BAL@ATL": (33.7553, -84.4006)}
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]
def g(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r: return json.loads(r.read().decode())
T0 = dt.datetime(2026, 10, 9, 12); T1 = dt.datetime(2026, 10, 12, 0)
def om(lat, lon):
    d = g("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=wind_speed_10m,wind_gusts_10m,precipitation,temperature_2m&wind_speed_unit=mph&precipitation_unit=inch&temperature_unit=fahrenheit&timezone=UTC&forecast_days=4&models=%s" % (lat, lon, ",".join(MODELS)))["hourly"]
    out = []
    for i, t in enumerate(d["time"]):
        tt = dt.datetime.strptime(t, "%Y-%m-%dT%H:%M")
        if not (T0 <= tt < T1): continue
        def vals(k): return [d[k + "_" + m][i] for m in MODELS if d.get(k + "_" + m) and d[k + "_" + m][i] is not None]
        def mean(v): return round(sum(v) / len(v), 2) if v else None
        w, gu, pr, te = vals("wind_speed_10m"), vals("wind_gusts_10m"), vals("precipitation"), vals("temperature_2m")
        out.append({"t": t + "Z", "wind": mean(w), "gust": mean(gu), "gust_max": max(gu) if gu else None, "rain": mean(pr), "rain_max": max(pr) if pr else None, "temp": mean(te), "models_wet": sum(1 for p in pr if p >= 0.01)})
    return out
def expand(vals):
    h = {}
    for v in vals:
        s, _, dur = v["validTime"].partition("/")
        st = dt.datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
        mm = re.search(r"PT?(?:(\d+)D)?T?(?:(\d+)H)?", dur)
        hrs = (int(mm.group(1) or 0) * 24 + int(mm.group(2) or 0)) or 1
        for k in range(hrs): h[(st + dt.timedelta(hours=k)).strftime("%Y-%m-%dT%H:00Z")] = v["value"]
    return h
def nws(lat, lon):
    p = g("https://api.weather.gov/points/%s,%s" % (lat, lon))["properties"]
    gd = g(p["forecastGridData"])["properties"]
    ws = expand(gd["windSpeed"]["values"]); wg = expand(gd["windGust"]["values"]); pop = expand(gd["probabilityOfPrecipitation"]["values"]); q = expand(gd["quantitativePrecipitation"]["values"])
    out = []; t = T0
    while t < T1:
        k = t.strftime("%Y-%m-%dT%H:00Z")
        out.append({"t": k, "wind": round(ws[k] * 0.621371, 1) if ws.get(k) is not None else None, "gust": round(wg[k] * 0.621371, 1) if wg.get(k) is not None else None, "pop": pop.get(k), "qpf_mm": q.get(k)})
        t += dt.timedelta(hours=1)
    return out
res = {"generated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "venues": {}}
for k, (la, lo) in V.items():
    r = {}
    try: r["om"] = om(la, lo)
    except Exception as e: r["om_err"] = str(e)[:80]
    try: r["nws"] = nws(la, lo)
    except Exception as e: r["nws_err"] = str(e)[:80]
    res["venues"][k] = r
    print(k, len(r.get("om", [])), len(r.get("nws", [])), r.get("om_err", ""), r.get("nws_err", ""))
json.dump(res, open("wxhourly.json", "w"), separators=(",", ":"))
