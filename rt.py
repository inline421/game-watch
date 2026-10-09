"""Real-time, held-bet-aware alerts (public repo: no theses/units/prices, only game id + side).
Rules (Jarred 10/9): only games we HOLD a bet on; only MAJOR changes (weather swing of 2 levels, stable >=10 min;
moved indoors/relocated/postponed/kick moved; QB/key player OUT); point-first, plain English; one batched message;
none 22:00-07:00 ET; no @mention bot text, no numbers dump."""
import os, re, json, datetime as dt, urllib.request, zoneinfo
HERE = os.path.dirname(os.path.abspath(__file__))
TZ = zoneinfo.ZoneInfo("America/New_York")
REPO = os.environ.get("GITHUB_REPOSITORY", "inline421/game-watch"); TOKEN = os.environ.get("GH_TOKEN", "")
HELD = json.load(open(os.path.join(HERE, "held.json")))
HOLD_MIN = 10  # weather swing must persist this long

def _now(): return dt.datetime.now(dt.timezone.utc)

def wx(st, title, notes, p0, p1):
    """Called on every weather level change. Queues a candidate; flush releases it only if still true after HOLD_MIN."""
    m = re.match(r"WEATHER (\S+) level \d \(kick in (-?[\d.]+)h", title)
    if not m: return
    gid, h = m.group(1), float(m.group(2))
    if gid not in HELD or h > 40 or h < -3: return
    ref = st.setdefault("_wxref", {}).setdefault(gid, p0)
    st.setdefault("_rtq", []).append({"k": "wx|%s" % gid, "gid": gid, "t": _now().isoformat(), "kind": "wx", "ref": ref, "lv": p1})

def news(st, title, why):
    m = re.match(r"(\S+) @ (\S+) \(kick in (-?[\d.]+)h", title)
    if not m: return
    gid = "%s@%s" % (m.group(1), m.group(2))
    if gid not in HELD: return
    for w in why:
        U = w.upper()
        if U.startswith("GAME STATUS"): kind = "post"
        elif U.startswith("MOVED INDOORS"): kind = "indoor"
        elif U.startswith("VENUE CHANGED"): kind = "venue"
        elif U.startswith("KICKOFF MOVED"): kind = "kick"
        elif U.startswith("QB "): kind = "qb"
        elif U.startswith("HEADLINE") and re.search(r"moved indoors|relocat|postpon|ruled out|inactive|will not play", w, re.I): kind = "head"
        else: continue
        st.setdefault("_rtq", []).append({"k": "%s|%s|%s" % (kind, gid, w[:40]), "gid": gid, "t": _now().isoformat(), "kind": kind, "w": w})

def _wx_msg(it, st):
    gid, ref, lv = it["gid"], it["ref"], it["lv"]
    bets = HELD[gid]
    unders = [b for b in bets if b.get("wx") and b["s"] == "U"]; overs = [b for b in bets if b.get("wx") and b["s"] == "O"]
    if ref >= 2 and lv <= 0:
        if unders: return ("WEATHER FADED: %s" % gid, "Storm impact has dropped off for this game. HEDGE OUT %s (weather edge is gone); keep only if you like the number without the weather." % ", ".join(b["b"] for b in unders))
        if overs: return ("WEATHER IMPROVED: %s" % gid, "Conditions got much better. KEEP %s, the weather risk to it is gone." % ", ".join(b["b"] for b in overs))
    if ref <= 0 and lv >= 2:
        if unders: return ("WEATHER WORSE: %s" % gid, "Storm is now expected to hit this game. KEEP %s; SIZE UP if still available at the number." % ", ".join(b["b"] for b in unders))
        if overs: return ("WEATHER WORSE: %s" % gid, "Rain/wind is now expected. HEDGE OUT or KILL %s, weather works against it." % ", ".join(b["b"] for b in overs))
    return None

def _news_msg(it):
    gid, k = it["gid"], it["kind"]; bets = HELD[gid]
    U = [b for b in bets if b["s"] == "U"]; O = [b for b in bets if b["s"] == "O"]
    nm = lambda L: ", ".join(b["b"] for b in L)
    if k == "post": return ("GAME POSTPONED/DELAYED: %s" % gid, "Bets void if postponed. Nothing to do now; re-check when rescheduled.")
    if k == "indoor":
        cond = [b for b in O if b.get("indoor_go")]
        if cond: return ("GAME MOVED INDOORS: %s" % gid, "GO: %s is live now (conditional triggered). Bet before the line moves." % nm(cond))
        if any(b.get("wx") for b in U): return ("GAME MOVED INDOORS: %s" % gid, "Weather Under is dead indoors. HEDGE OUT %s; consider the Over." % nm([b for b in U if b.get("wx")]))
        return ("GAME MOVED INDOORS: %s" % gid, "Check %s; weather no longer applies." % nm(bets))
    if k in ("venue", "kick"): return ("%s CHANGED: %s" % ("VENUE" if k == "venue" else "KICKOFF", gid), "%s Re-check weather and %s." % (it["w"][:80], nm(bets)))
    if k == "qb":
        p = it["w"][:60]
        out = []
        if U: out.append("KEEP/SIZE UP %s" % nm(U))
        if O: out.append("HEDGE OUT %s" % nm(O))
        if not out: out.append("re-check %s" % nm(bets))
        return ("QB OUT: %s (%s)" % (gid, p), "; ".join(out) + ". Scoring drops with a backup QB.")
    if k == "head": return ("NEWS: %s" % gid, "%s. Check %s." % (it["w"][:110], nm(bets)))
    return None

def _post(title, body):
    if not TOKEN: print("no token"); return
    try:
        req = urllib.request.Request("https://api.github.com/repos/%s/issues" % REPO,
            data=json.dumps({"title": title[:200], "body": body, "assignees": ["inline421"]}).encode(),
            headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "game-watch"})
        urllib.request.urlopen(req, timeout=25).read()
    except Exception as e: print("issue fail", str(e)[:80])

def flush(st, pend):
    for t, w, is_wx in [(p["t"], p["w"], p["wx"]) for p in pend]:
        if not is_wx: news(st, t, w)
    q = st.get("_rtq", [])
    if not q: return
    n = _now(); loc = n.astimezone(TZ)
    if loc.hour >= 22 or loc.hour < 7: return  # nothing overnight; items wait for 7 AM (stale ones are rechecked)
    sent = st.setdefault("_rtsent", {}); keep = []; msgs = []
    for it in q:
        age = (n - dt.datetime.fromisoformat(it["t"])).total_seconds() / 60
        if it["kind"] == "wx":
            if age < HOLD_MIN: keep.append(it); continue
            if st.get("_wx", {}).get(it["gid"]) != it["lv"]: continue  # reverted: drop
            m = _wx_msg(it, st)
            if m: msgs.append((it, m))
            st["_wxref"][it["gid"]] = it["lv"]
        else:
            if it["k"] in sent: continue
            m = _news_msg(it)
            if m: msgs.append((it, m)); sent[it["k"]] = n.isoformat()
    st["_rtq"] = keep
    if not msgs: return
    title = msgs[0][1][0] + (" (+%d more)" % (len(msgs) - 1) if len(msgs) > 1 else "")
    body = "\n\n".join("**%s**\n%s" % m for _, m in msgs)
    _post(title, body); print("RT ALERT", title)
