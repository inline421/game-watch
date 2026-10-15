"""1H closing-line snapshot (Jarred 10/14): for held bets that mention 1H, record The Odds API first-half lines
in the last 12 minutes before kickoff. Latest read before kick wins = the 1H 'close' used for CLV.
Stored in state.json under _h1[<AWAY@HOME>]. Needs env ODDS_API_KEY. Cost ~4 credits per read, max ~3 reads per game."""
import os, json, re, urllib.request, datetime as dt
KEY = os.environ.get("ODDS_API_KEY", "")
BASE = "https://api.the-odds-api.com/v4/sports/%s"
SPORT = {"nfl": "americanfootball_nfl", "college-football": "americanfootball_ncaaf"}
MK = "totals_h1,spreads_h1,team_totals_h1,h2h_h1"
HERE = os.path.dirname(os.path.abspath(__file__))

def _get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "game-watch"}), timeout=25) as r:
        return json.loads(r.read().decode()), r.headers.get("x-requests-remaining")

def run(st, sb, now):
    if not KEY:
        return
    try:
        held = json.load(open(os.path.join(HERE, "held.json")))
    except Exception:
        return
    want = {g: [b.get("b", "") for b in bets if re.search(r"\b1H\b", b.get("b", ""), re.I)] for g, bets in held.items()}
    want = {g: v for g, v in want.items() if v}
    if not want:
        return
    h1 = st.setdefault("_h1", {})
    for (lg, d), evs in sb.items():
        sp = SPORT.get(lg)
        if not sp:
            continue
        for e in evs:
            c = e["competitions"][0]
            abbr = {x["homeAway"]: x["team"]["abbreviation"] for x in c["competitors"]}
            names = {x["homeAway"]: x["team"]["displayName"] for x in c["competitors"]}
            g = "%s@%s" % (abbr.get("away"), abbr.get("home"))
            if g not in want:
                continue
            kick = dt.datetime.strptime(e["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
            mins = (kick - now).total_seconds() / 60
            if not (0 < mins <= 12):
                continue
            rec = h1.get(g, {})
            if rec.get("at") and (now - dt.datetime.fromisoformat(rec["at"])).total_seconds() < 240:
                continue
            try:
                evl, _ = _get(BASE % sp + "/events?apiKey=" + KEY)
                oid = next((x["id"] for x in evl if x.get("home_team") == names.get("home") and x.get("away_team") == names.get("away")), None)
                if not oid:
                    h1[g] = {"at": now.isoformat(), "err": "no Odds API event match for %s vs %s" % (names.get("away"), names.get("home")), "bets": want[g]}
                    continue
                od, left = _get(BASE % sp + "/events/%s/odds?apiKey=%s&regions=us&markets=%s&oddsFormat=american" % (oid, KEY, MK))
                lines = []
                for bk in od.get("bookmakers", []):
                    for m in bk.get("markets", []):
                        for o in m.get("outcomes", []):
                            lines.append([bk.get("key"), m.get("key"), o.get("name"), o.get("description"), o.get("point"), o.get("price")])
                h1[g] = {"at": now.isoformat(), "kick": e["date"], "mins_to_kick": round(mins, 1), "bets": want[g], "lines": lines, "credits_left": left}
                print("h1 snapshot", g, len(lines), "lines, credits left", left)
            except Exception as ex:
                h1[g] = {"at": now.isoformat(), "err": str(ex)[:120], "bets": want[g]}
