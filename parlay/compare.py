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
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "30"))  # alt-line pulls per run (4 credits each)
CREDIT_FLOOR = int(os.environ.get("CREDIT_FLOOR", "2000"))  # never drain the shared key below this
HEAVY = -200              # leg qualifies if a book prices it -200 or shorter
FLOOR = -3000
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
    rows, pulls = [], 0
    for label, (okey, kpre) in SPORTS.items():
        kev = load_kalshi(kpre)
        if not kev:
            continue
        base = get(f"{OA}/sports/{okey}/odds?apiKey={KEY}&regions=us&markets=h2h&oddsFormat=american"
                   f"&bookmakers={','.join(BOOKS)}&commenceTimeFrom={fmt(now)}&commenceTimeTo={fmt(t_to)}")
        if not base:
            continue
        for g in sorted(base, key=lambda x: x["commence_time"]):
            if pulls >= MAX_EVENTS or (credits_left is not None and credits_left < CREDIT_FLOOR):
                break
            ko = dt.datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00")).astimezone(ET)
            teams = [g["home_team"], g["away_team"]]
            match = None
            for k, e in kev.items():
                if e["date"] != ko.date() or len(set(e["teams"])) < 2:
                    continue
                mapped = {find_team(t, teams) for t in set(e["teams"])}
                if mapped == set(teams):
                    match = e
                    break
            if not match:
                continue
            od = get(f"{OA}/sports/{okey}/events/{g['id']}/odds?apiKey={KEY}&regions=us&oddsFormat=american"
                     f"&markets=h2h,spreads,totals,alternate_spreads,alternate_totals&bookmakers={','.join(BOOKS)}")
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
                    rows.append({
                        "ts_utc": fmt(now), "sport": label, "game": game,
                        "kickoff_et": ko.strftime("%Y-%m-%d %H:%M"), "leg": desc,
                        "kalshi_ticker": m["ticker"], "kalshi_side": side, "kalshi_ask": a,
                        "kalshi_dec": round(1 / a, 4), "kalshi_net_dec": round(kalshi_net(a), 4),
                        "fd_price": fd if fd is not None else "", "fd_dec": round(am_to_dec(fd), 4) if fd is not None else "",
                        "dk_price": dk if dk is not None else "", "dk_dec": round(am_to_dec(dk), 4) if dk is not None else "",
                    })
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
    if not os.path.exists(DATA):
        body, subj = "No data logged yet.", "PARLAY STUDY: no data yet"
    else:
        cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        rows = [r for r in csv.DictReader(open(DATA))
                if dt.datetime.strptime(r["ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) >= cut]
        lines = []
        head = None
        for book, col in (("FanDuel", "fd_dec"), ("DraftKings", "dk_dec")):
            pr = [(float(r["kalshi_net_dec"]), float(r[col])) for r in rows if r[col]]
            if not pr:
                continue
            ratio = gmean([k / b for k, b in pr])
            wins = sum(1 for k, b in pr if k > b) / len(pr)
            bdec = gmean([b for _, b in pr])
            p3_book, p3_k = bdec ** 3, (bdec * ratio) ** 3
            profit_gain = (p3_k - 1) / (p3_book - 1) - 1
            lines.append(f"- vs {book}: Kalshi (after fee) paid more on {wins:.0%} of {len(pr)} legs; "
                         f"typical 3-leg parlay {p3_book:.2f}x on {book} vs {p3_k:.2f}x on Kalshi "
                         f"({profit_gain:+.0%} profit)")
            if head is None or profit_gain > head[1]:
                head = (book, profit_gain)
        by = {}
        for r in rows:
            if r["fd_dec"]:
                by.setdefault(r["sport"], []).append(float(r["kalshi_net_dec"]) / float(r["fd_dec"]))
        for s, xs in sorted(by.items()):
            lines.append(f"- {s}: per-leg Kalshi vs FanDuel {gmean(xs) - 1:+.1%} ({len(xs)} legs)")
        if head:
            verdict = "KALSHI PAYS MORE" if head[1] > 0 else "BOOKS PAY MORE"
            subj = f"PARLAY STUDY: {verdict} on heavy-favorite parlays ({head[1]:+.0%} profit vs {head[0]})"
        else:
            subj = "PARLAY STUDY: no matched legs this week"
        body = "\n".join(lines) + "\n\nKalshi priced from single-leg asks incl. approx. fee; actual Kalshi combo quotes can differ."
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
