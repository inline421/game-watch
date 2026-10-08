#!/usr/bin/env python3
"""Game news watcher (public, no bet data). Polls ESPN + Google News for drastic changes on games in games.json:
postponed/delayed, moved indoors / venue change, kickoff moved, big total move, NFL QB status. Alerts -> GitHub issue (@mention) + ntfy push."""
import os, sys, json, time, re, hashlib, subprocess, urllib.request, urllib.parse, datetime as dt
import xml.etree.ElementTree as ET
import wx
HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "application/json,text/xml,*/*"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/%s"
ALIAS = {"BAMA": "ALA", "UL": "LA", "LT": "LT", "SC": "SC"}
BAD = ("POSTPONED", "CANCELED", "CANCELLED", "SUSPENDED", "DELAYED", "DELAY", "FORFEIT", "ABANDONED")
BAD_INJ = ("out", "doubtful", "injured reserve", "suspended", "inactive")
KEYS = re.compile(r"moved indoors|relocat|postpon|cancel|ruled out|will not play|won't play|inactive|suspend|scratch|benched|out for|lost for the season|game delayed|moved to", re.I)
FB = re.compile(r"football|kickoff|stadium|quarterback|\bQB\b|\bbowl\b|\bNFL\b|\bNCAA\b|\bCFB\b|touchdown|gridiron|running back|wide receiver|offensive line|head coach", re.I)
# noise: non-football stories (politics etc.), hypotheticals, and College GameDay venue news (GameDay moving is NOT the game moving)
NOISE = re.compile(r"voter|election|ballot|senate|congress|legislat|governor|police|arrest|lawsuit|gameday|what happens if|what if|\?\s*(-|$)|odds|picks?\b|prediction|preview|how to watch|betting", re.I)
REPO = os.environ.get("GITHUB_REPOSITORY", "inline421/game-watch"); TOKEN = os.environ.get("GH_TOKEN", "")
NTFY = os.environ.get("NTFY_TOPIC", "")
LIVE = int(os.environ.get("LIVE_SECONDS") or 20400); POLL = int(os.environ.get("POLL_SECONDS") or 30)
SF = os.path.join(HERE, "state.json")

def get(url, tries=2):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r: return r.read().decode()
        except Exception as e: last = e; time.sleep(2)
    raise last
def gj(u): return json.loads(get(u))
def ab(x): return ALIAS.get(x, x)
def now(): return dt.datetime.now(dt.timezone.utc)

def notify(title, body):
    if TOKEN:
        try:
            req = urllib.request.Request("https://api.github.com/repos/%s/issues" % REPO, data=json.dumps({"title": title[:200], "body": "@inline421 " + body}).encode(),
                headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "game-watch"})
            urllib.request.urlopen(req, timeout=25).read()
        except Exception as e: print("issue fail", e)
    if NTFY:
        try:
            urllib.request.urlopen(urllib.request.Request("https://ntfy.sh/" + NTFY, data=(title[:180] + " - check your email/sheet for the play").encode(),
                headers={"Title": "DRASTIC CHANGE ALERT", "Priority": "urgent", "Tags": "rotating_light"}), timeout=15).read()
        except Exception as e: print("ntfy fail", e)
    print("ALERT", title)

def check(games, st, sb_cache, hl):
    out = []
    n = now()
    for g in games:
        lg, away, home, d = g["league"], g["away"], g["home"], g["date"]
        key = "%s|%s@%s|%s" % (lg, away, home, d)
        evs = sb_cache.get((lg, d), [])
        ev = None
        for e in evs:
            ab_ = {ab(x["team"]["abbreviation"]) for x in e["competitions"][0]["competitors"]}
            if ab(away) in ab_ and ab(home) in ab_: ev = e; break
        if not ev: continue
        comp = ev["competitions"][0]
        kick = dt.datetime.strptime(ev["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
        h = (kick - n).total_seconds() / 3600
        if h < -5 or h > 60: continue
        s0 = st.get(key, {})
        names = {x["team"]["abbreviation"]: x["team"].get("shortDisplayName", x["team"]["abbreviation"]) for x in comp["competitors"]}
        od = (comp.get("odds") or [{}])[0]
        cur = {"status": comp["status"]["type"]["name"], "venue": comp.get("venue", {}).get("fullName", ""), "indoor": bool(comp.get("venue", {}).get("indoor")),
               "kick": ev["date"], "total": od.get("overUnder"), "seen": s0.get("seen", []), "inj": s0.get("inj")}
        why = []
        if any(b in cur["status"].upper() for b in BAD) and not s0.get("status", "").upper().startswith(cur["status"].upper()): why.append("GAME STATUS: " + cur["status"])
        if s0.get("venue") is not None:
            if cur["indoor"] and not s0.get("indoor"): why.append("MOVED INDOORS (%s -> %s)" % (s0["venue"], cur["venue"]))
            elif cur["venue"] and cur["venue"] != s0["venue"]: why.append("VENUE CHANGED %s -> %s" % (s0["venue"], cur["venue"]))
            if cur["kick"] != s0["kick"]: why.append("KICKOFF MOVED %s -> %s (UTC)" % (s0["kick"], cur["kick"]))
            if cur["total"] and s0.get("total") and abs(cur["total"] - s0["total"]) >= 2: why.append("TOTAL MOVED %s -> %s" % (s0["total"], cur["total"]))
        cur["base_total"] = s0.get("base_total") or cur["total"]
        if cur["total"] and cur["base_total"] and abs(cur["total"] - cur["base_total"]) >= 3 and not any(w.startswith("TOTAL") for w in why) and cur["total"] != s0.get("total"):
            why.append("TOTAL MOVED %s -> %s since first read" % (cur["base_total"], cur["total"]))
        if lg == "nfl" and "nfl" in hl["inj"]:
            snap = {}
            for x in comp["competitors"]:
                for r in hl["inj"]["nfl"].get(x["team"]["displayName"], []):
                    a = r.get("athlete", {}); pos = (a.get("position") or {}).get("displayName", "")
                    if pos == "Quarterback": snap[a.get("displayName", "")] = r.get("status", "")
            if s0.get("inj") is not None:
                for nm, stt in snap.items():
                    if s0["inj"].get(nm) != stt and stt.lower() in BAD_INJ: why.append("QB %s now %s" % (nm, stt.upper()))
            cur["inj"] = snap
        if hl["news"]:
            try:
                q = '(%s) (moved indoors OR relocated OR postponed OR "ruled out" OR inactive OR suspended) when:1d' % " OR ".join('"%s"' % x for x in names.values())
                rss = ET.fromstring(get("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en-US&gl=US&ceid=US:en", tries=1))
                seen = set(cur["seen"]); new = []
                for it in rss.iter("item"):
                    t = it.findtext("title", "")
                    hid = hashlib.md5(t.encode()).hexdigest()[:10]
                    if hid in seen: continue
                    seen.add(hid)
                    tl = t.lower()
                    hits = [x for x in names.values() if x.lower() in tl]
                    # need football context, no noise, and BOTH teams named (or one team plus an explicit football word)
                    if KEYS.search(t) and FB.search(t) and not NOISE.search(t) and (len(hits) >= 2 or (hits and re.search(r"football|quarterback|\bQB\b|\bNFL\b", t, re.I))): new.append(t)
                cur["seen"] = list(seen)[-80:]
                if s0.get("kick"):
                    for t in new[:3]: why.append("HEADLINE: " + t[:140])
            except Exception as e: print("news fail", key, str(e)[:60])
        st[key] = cur
        if why: out.append(("%s @ %s (kick in %.1fh)" % (away, home, h), why))
    return out

def save(st):
    json.dump(st, open(SF, "w"))
    subprocess.run("git config user.name watch-bot; git config user.email watch-bot@users.noreply.github.com; git add state.json; "
                   "git diff --cached --quiet || (git commit -qm 'state' && git pull --rebase -q && git push -q)", shell=True, cwd=HERE)

def main():
    games = json.load(open(os.path.join(HERE, "games.json")))
    st = json.load(open(SF)) if os.path.exists(SF) else {}
    t0 = time.time(); last_save = t0; i = 0; last_wx = 0
    wxg = json.load(open(os.path.join(HERE, 'wx_games.json')))
    while True:
        n = now(); today = (n - dt.timedelta(hours=10)).strftime("%Y-%m-%d"); hor = (n + dt.timedelta(days=3)).strftime("%Y-%m-%d")
        act = [g for g in games if today <= g["date"] <= hor]
        sb = {}; hl = {"inj": {}, "news": i % 4 == 0}
        for lg, d in {(g["league"], g["date"]) for g in act}:
            try:
                sb[(lg, d)] = gj((ESPN % lg) + "/scoreboard?dates=%s&limit=400%s" % (d.replace("-", ""), "&groups=80" if lg != "nfl" else "")).get("events", [])
            except Exception as e: print("sb fail", lg, d, str(e)[:60])
        if any(g["league"] == "nfl" for g in act):
            try: hl["inj"]["nfl"] = {t["displayName"]: t["injuries"] for t in gj(ESPN % "nfl" + "/injuries")["injuries"]}
            except Exception as e: print("inj fail", str(e)[:60])
        for title, why in check(act, st, sb, hl):
            notify("DRASTIC CHANGE ALERT: %s - %s" % (title, why[0]), "**%s**\n\n" % title + "\n".join("- " + w for w in why))
        if time.time() - last_wx > 600:
            last_wx = time.time()
            try:
                for title, notes in wx.run(wxg, st, now()):
                    notify('DRASTIC CHANGE ALERT: ' + title, '**%s**\n\n' % title + '\n'.join('- ' + w for w in notes))
            except Exception as e: print('wx err', str(e)[:80])
        i += 1
        if time.time() - last_save > 1800: save(st); last_save = time.time()
        if time.time() - t0 + POLL > LIVE: break
        time.sleep(POLL if act else 300)
    save(st)
main()
