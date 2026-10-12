#!/usr/bin/env python3
"""Heavy-favorite parlay price study: Kalshi vs FanDuel vs DraftKings.
scan    -> append matched heavy-favorite legs to parlay/data/legs.csv
summary -> email weekly result to j1primeai (point-first)
"""
import os, sys, re, csv, json, math, time, smtplib, urllib.request
import datetime as dt
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OA = "https://api.the-odds-api.com/v4"
KS = "https://api.elections.kalshi.com/trade-api/v2"
KEY = os.environ.get("ODDS_API_KEY", "")
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "legs.csv")
SPORTS = {  # label: (odds-api sport key, kalshi series prefix)
    "NFL": ("americanfootball_nfl", "KXNFL"),
    "NCAAF": ("americanfootball_ncaaf", "KXNCAAF"),
    "MLB": ("baseball_mlb", "KXMLB"),
    "NHL": ("icehockey_nhl", "KXNHL"),
    "NBA": ("basketball_nba", "KXNBA"),
}
HORIZON_H = 30            # only games starting in the next 30h
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "4"))   # games pulled per day (3 credits each)
MAX_LEGS = int(os.environ.get("MAX_LEGS", "20"))      # heavy-favorite legs logged per day
PER_GAME = 5                                          # max legs kept per game
CREDIT_FLOOR = int(os.environ.get("CREDIT_FLOOR", "2000"))  # never drain the shared key below this
HEAVY = -200              # leg qualifies if a book prices it -200 or shorter
FLOOR = -1500             # ignore near-locks heavier than this
BOOKS = ("fanduel", "draftkings")
FIELDS = ["ts_utc", "sport", "game", "kickoff_et", "leg", "kalshi_ticker", "kalshi_side",
          "kalshi_ask", "kalshi_dec", "kalshi_net_dec", "fd_price", "fd_dec", "dk_price", "dk_dec"]
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
credits_left = None


def get(url, tries=3):
    global credits_left
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "parlay-compare/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                rem = r.headers.get("x-requests-remaining")
                if rem is not None:
                    credits_left = int(float(rem))
                return json.loads(r.read().decode())
        except Exception as e:  # retry, then give up on this call only
            last = e
            time.sleep(2 * (i + 1))
    print("GET failed:", url.split("apiKey=")[0], last)
    return None


def am_to_dec(p):
    p = float(p)
    return 1 + (p / 100 if p > 0 else 100 / abs(p))


def kalshi_net(ask):
    fee = 0.07 * ask * (1 - ask)   # taker fee approx; per Kalshi fee schedule [verify]
    return 1 / (ask + fee)


# ---------------- Kalshi ----------------
def kalshi_series(series):
    out, cursor = [], ""
    for _ in range(15):
        j = get(f"{KS}/markets?series_ticker={series}&status=open&limit=200" + (f"&cursor={cursor}" if cursor else ""))
        if not j:
            break
        out += j.get("markets", [])
        cursor = j.get("cursor") or ""
        if not cursor:
            break
    return out


def key_date(key):
    m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", key)
    if not m:
        return None
    return dt.date(2000 + int(m.group(1)), MON[m.group(2)], int(m.group(3)))


def load_kalshi(prefix):
    """-> {event_key: {'date', 'teams': [..], 'markets': [(kind, team, line, market)]}}"""
    ev = {}
    for kind in ("GAME", "SPREAD", "TOTAL"):
        for m in kalshi_series(prefix + kind):
            k = m["event_ticker"].split("-", 1)[1]
            e = ev.setdefault(k, {"date": key_date(k), "teams": [], "markets": []})
            ys = (m.get("yes_sub_title") or "").strip()
            if kind == "GAME":
                if ys and ys.lower() not in ("tie", "draw"):
                    e["teams"].append(ys)
                    e["markets"].append(("ML", ys, None, m))
            elif kind == "SPREAD":
                r = re.match(r"(.+?) wins by over ([\d.]+)", ys)
                if r:
                    e["markets"].append(("SPREAD", r.group(1).strip(), float(r.group(2)), m))
            else:
                r = re.match(r"Over ([\d.]+)", ys)
                if r:
                    e["markets"].append(("TOTAL", None, float(r.group(1)), m))
    return ev


def name_match(kname, full):
    return full.lower().strip().startswith(kname.lower().strip())


def find_team(kname, teams):
    hits = [t for t in teams if name_match(kname, t)]
    if len(hits) == 1:
        return hits[0]
    return None


def ask(m, side):
    v = m.get("yes_ask_dollars" if side == "yes" else "no_ask_dollars")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if 0.01 <= v <= 0.995 else None


# ---------------- scan ----------------
def scan():
    if not KEY:
        sys.exit("ODDS_API_KEY missing")
    now = dt.datetime.now(dt.timezone.utc)
    t_to = now + dt.timedelta(hours=HORIZON_H)
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, pulls, cands = [], 0, {}
    for label, (okey, kpre) in SPORTS.items():
        kev = load_kalshi(kpre)
        if not kev:
            continue
        base = get(f"{OA}/sports/{okey}/odds?apiKey={KEY}&regions=us&markets=h2h&oddsFormat=american"
                   f"&bookmakers={','.join(BOOKS)}&commenceTimeFrom={fmt(now)}&commenceTimeTo={fmt(t_to)}")
        for g in sorted(base or [], key=lambda x: x["commence_time"]):
            ko = dt.datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00")).astimezone(ET)
            teams = [g["home_team"], g["away_team"]]
            for k, e in kev.items():
                if e["date"] != ko.date() or len(set(e["teams"])) < 2:
                    continue
                if {find_team(t, teams) for t in set(e["teams"])} == set(teams):
                    cands.setdefault(label, []).append((okey, g, ko, teams, e))
                    break
    picks = []  # round-robin across sports so the day's legs span sports/games
    while len(picks) < MAX_EVENTS and any(cands.values()):
        for label in list(cands):
            if cands[label] and len(picks) < MAX_EVENTS:
                picks.append((label,) + cands[label].pop(0))
    for label, okey, g, ko, teams, match in picks:
        if len(rows) >= MAX_LEGS or (credits_left is not None and credits_left < CREDIT_FLOOR):
            break
        if True:
            od = get(f"{OA}/sports/{okey}/events/{g['id']}/odds?apiKey={KEY}&regions=us&oddsFormat=american"
                     f"&markets=h2h,alternate_spreads,alternate_totals&bookmakers={','.join(BOOKS)}")
            pulls += 1
            if not od:
                continue
            px = {}  # (book, kind, name, point) -> price
            for b in od.get("bookmakers", []):
                for mk in b.get("markets", []):
                    kind = {"h2h": "ML", "spreads": "SP", "alternate_spreads": "SP",
                            "totals": "TOT", "alternate_totals": "TOT"}[mk["key"]]
                    for o in mk["outcomes"]:
                        px[(b["key"], kind, o["name"], o.get("point"))] = o["price"]
            game = f"{g['away_team']} @ {g['home_team']}"
            glegs = []
            for kind, kname, line, m in match["markets"]:
                legs = []
                if kind == "ML":
                    t = find_team(kname, teams)
                    if t:
                        legs.append((f"{t} ML", "yes", ("ML", t, None)))
                elif kind == "SPREAD":
                    t = find_team(kname, teams)
                    if t:
                        opp = teams[1] if t == teams[0] else teams[0]
                        legs.append((f"{t} -{line}", "yes", ("SP", t, -line)))
                        legs.append((f"{opp} +{line}", "no", ("SP", opp, line)))
                else:
                    legs.append((f"Over {line}", "yes", ("TOT", "Over", line)))
                    legs.append((f"Under {line}", "no", ("TOT", "Under", line)))
                for desc, side, (bk, nm, pt) in legs:
                    a = ask(m, side)
                    if a is None:
                        continue
                    fd = px.get(("fanduel", bk, nm, pt))
                    dk = px.get(("draftkings", bk, nm, pt))
                    prices = [p for p in (fd, dk) if p is not None]
                    if not prices or min(prices) > HEAVY or min(prices) < FLOOR:
                        continue
                    glegs.append({
                        "ts_utc": fmt(now), "sport": label, "game": game,
                        "kickoff_et": ko.strftime("%Y-%m-%d %H:%M"), "leg": desc,
                        "kalshi_ticker": m["ticker"], "kalshi_side": side, "kalshi_ask": a,
                        "kalshi_dec": round(1 / a, 4), "kalshi_net_dec": round(kalshi_net(a), 4),
                        "fd_price": fd if fd is not None else "", "fd_dec": round(am_to_dec(fd), 4) if fd is not None else "",
                        "dk_price": dk if dk is not None else "", "dk_dec": round(am_to_dec(dk), 4) if dk is not None else "",
                    })
            glegs.sort(key=lambda r: min(p for p in (r["fd_price"], r["dk_price"]) if p != ""))
            step = max(1, len(glegs) // PER_GAME)
            rows += glegs[::step][:PER_GAME][:MAX_LEGS - len(rows)]
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    new = not os.path.exists(DATA)
    with open(DATA, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"scan done: {pulls} games pulled, {len(rows)} heavy-favorite legs logged, credits left {credits_left}")


# ---------------- summary ----------------
def gmean(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else None


def summary(days=7):
    import itertools, random
    rows = []
    if os.path.exists(DATA):
        cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        rows = [r for r in csv.DictReader(open(DATA))
                if dt.datetime.strptime(r["ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) >= cut]
    # build 3-leg parlays from each day's legs (different games, all 3 venues priced)
    rnd, pars = random.Random(42), []
    byday = {}
    for r in rows:
        if r["fd_dec"] and r["dk_dec"]:
            byday.setdefault(r["ts_utc"][:10], []).append(r)
    for day, legs in byday.items():
        combos = [c for c in itertools.combinations(legs, 3) if len({x["game"] for x in c}) == 3]
        for c in rnd.sample(combos, min(10, len(combos))):
            k = math.prod(float(x["kalshi_net_dec"]) for x in c)
            fd = math.prod(float(x["fd_dec"]) for x in c)
            dk = math.prod(float(x["dk_dec"]) for x in c)
            pars.append((k, fd, dk, c))
    if not pars:
        subj, body = "PARLAY STUDY: not enough legs yet", f"{len(rows)} legs logged this week; need legs from 3+ games priced at all three venues."
    else:
        n = len(pars)
        kwin = sum(1 for k, fd, dk, _ in pars if k > max(fd, dk)) / n
        gfd = gmean([(k - 1) / (fd - 1) for k, fd, dk, _ in pars]) - 1
        gdk = gmean([(k - 1) / (dk - 1) for k, fd, dk, _ in pars]) - 1
        avg = lambda i: sum(p[i] for p in pars) / n
        best = max(pars, key=lambda p: p[0] / max(p[1], p[2]))
        verdict = "KALSHI PAYS MORE" if min(gfd, gdk) > 0 else ("BOOKS PAY MORE" if max(gfd, gdk) < 0 else "MIXED")
        subj = f"PARLAY STUDY: {verdict} ({gfd:+.0%} profit vs FanDuel, {gdk:+.0%} vs DraftKings)"
        body = "\n".join([
            f"- {n} three-leg heavy-favorite parlays tested this week ({len(rows)} legs logged)",
            f"- Kalshi paid the most on {kwin:.0%} of them",
            f"- Avg payout: Kalshi {avg(0):.2f}x | FanDuel {avg(1):.2f}x | DraftKings {avg(2):.2f}x",
            "- Biggest gap: " + " + ".join(x["leg"] for x in best[3])
            + f" -> Kalshi {best[0]:.2f}x vs FanDuel {best[1]:.2f}x / DraftKings {best[2]:.2f}x",
            "",
            "Kalshi priced from single-leg asks incl. approx. fee; real Kalshi combo quotes can differ."])
    print(subj + "\n" + body)
    u, p = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if u and p:
        msg = MIMEText(body)
        msg["Subject"], msg["From"], msg["To"] = subj, u, "j1primeai@gmail.com"
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(u, p)
            s.send_message(msg)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    summary() if mode == "summary" else scan()
