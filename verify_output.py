#!/usr/bin/env python3
"""verify_output.py - hard PASS/FAIL gate for model-run output tabs (piece 1 of To Foreman row 64).

Usage:
  python3 verify_output.py tab.json [--now "YYYY-MM-DD HH:MM"] [--max-price-age-h 6]
tab.json = the tab as a JSON 2D array (Google Sheets values.get 'values', or {"values": [...]}).
Prints PASS, or FAIL plus one line per failing check (check id | cell | what is wrong).
Exit code 0 = PASS, 1 = FAIL, 2 = bad input.
Stdlib only. Checks (spec: memory /topics/agent-ops-lessons.md, ADOPTION 1; playbook on-demand-model-run.md 0b/0g/0h/5):
  R1 receipt in A1 with GAMES n of N, MARKETS, INPUTS (QB, last-4, injuries, weather, price time), NEEDS YOU
  R2 FULL SCAN needs n == N
  R3 price timestamp within --max-price-age-h of the STATUS time
  R4 every cents price has American odds beside it; edges never written as 'c'
  R5 banned strings
  R6 PLAYS / BELOW-GATE LIKES / TOP 10 sections present; top 10 has 10 lines; <=2 plays needs the opening sentence
  R7 receipt PLAYS POSTED count equals PLAY rows in the PLAYS section
  R8 every player-prop leg in PLAYS / BELOW-GATE LIKES / TOP 10 has a ROLE CHECK line (0g)
  R9 units: no 'strong' tier, max 1u per play (0h.3)
  R10 below-gate likes carry 'I would do X units if you approve'
"""
import json, re, sys, datetime as dt

BANNED = [r"\bstale\b(?! snapshot)", r"no research", r"not priced", r"data was bad", r"\b0 plays\b", r"\bNO PLAYS\b",
          r"\bCOMPLETE\b", r"\bINCOMPLETE\b"]  # 0b: COMPLETE/INCOMPLETE/BLOCKED retired as statuses
OPENER = "Only a couple of plays passed the model, but here are the top 10 by win probability, highest to lowest"
PROP_WORDS = re.compile(r"\b(rush|rec|receptions?|receiving|pass(ing)?|yds|yards|attempts?|att|comp|longest|TD|touchdown|INT)\b", re.I)
AMERICAN = re.compile(r"[+\-−]\d{3,4}")
CENTS = re.compile(r"(\d{1,2})\s?(?:c|¢)\b(?!\w)")


def load(path):
    d = json.load(open(path))
    if isinstance(d, dict):
        d = d.get("values", d.get("results", []))
    return [[str(c) for c in r] for r in d]


def cell(r, c):
    return "%s%d" % (chr(65 + c) if c < 26 else "A" + chr(65 + c - 26), r + 1)


def parse_time(s):
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s*([AP]M)?", s)
    if not m:
        return None
    h = int(m.group(2)); mi = int(m.group(3)); ap = m.group(4)
    if ap:
        h = h % 12 + (12 if ap == "PM" else 0)
    return dt.datetime.strptime(m.group(1), "%Y-%m-%d") + dt.timedelta(hours=h, minutes=mi)


def verify(rows, now=None, max_age_h=6):
    fails = []
    def F(rule, where, msg):
        fails.append("%s | %s | %s" % (rule, where, msg))

    flat = [(r, c, v) for r, row in enumerate(rows) for c, v in enumerate(row) if v.strip()]
    a1 = rows[0][0] if rows and rows[0] else ""
    # R1 receipt
    if not a1:
        F("R1", "A1", "empty; no STATUS/RECEIPT")
    else:
        if not re.search(r"GAMES\s+\d+\s+of\s+\d+", a1, re.I): F("R1", "A1", "missing 'GAMES n of N'")
        if not re.search(r"MARKETS", a1, re.I): F("R1", "A1", "missing MARKETS")
        for key, pat in [("QB", r"\bQB"), ("last-4", r"last-?4"), ("injuries", r"injur"), ("weather", r"weather"),
                         ("price time", r"(fresh )?price time")]:
            if not re.search(pat, a1, re.I): F("R1", "A1", "INPUTS missing " + key)
        if not re.search(r"NEEDS YOU", a1, re.I) and not re.search(r"no Needs You", a1, re.I):
            F("R1", "A1", "missing NEEDS YOU (or 'none')")
        # R2
        m = re.search(r"GAMES\s+(\d+)\s+of\s+(\d+)", a1, re.I)
        if m and int(m.group(1)) != int(m.group(2)) and re.search(r"FULL SCAN", a1) and "WITH ASSUMPTIONS" not in a1:
            F("R2", "A1", "FULL SCAN claimed but GAMES %s of %s" % m.groups())
        if m and int(m.group(1)) != int(m.group(2)) and re.search(r"\b(DONE|STATUS: COMPLETE)\b", a1):
            F("R2", "A1", "marked done with GAMES %s of %s" % m.groups())
        # R3
        st = parse_time(re.sub(r"^STATUS:\s*\w+\s*", "", a1))
        pm = re.search(r"price time\s*:?\s*(?:was\s*)?(\d{1,2}:\d{2}\s*[AP]M)", a1, re.I)
        if pm and st:
            pt = parse_time(st.strftime("%Y-%m-%d ") + pm.group(1))
            if pt and pt > st: pt -= dt.timedelta(days=1)
            ref = now or st
            if pt and (ref - pt).total_seconds() > max_age_h * 3600:
                F("R3", "A1", "price time %s is %.1fh older than reference %s (limit %sh)" % (pm.group(1), (ref - pt).total_seconds() / 3600, ref, max_age_h))
        elif not pm:
            F("R3", "A1", "no parsable 'price time' to check freshness")
    # R5 banned
    for r, c, v in flat:
        for b in BANNED:
            m = re.search(b, v)
            if m: F("R5", cell(r, c), "banned string '%s'" % m.group(0))
    # R4 cents need American odds
    for r, c, v in flat:
        for m in CENTS.finditer(v):
            window = v[m.start(): m.end() + 30]
            if not AMERICAN.search(window):
                F("R4", cell(r, c), "price '%s' has no American odds beside it" % m.group(0))
        if re.search(r"\b\d+(\.\d+)?\s?c\b.*\bedge\b|\bedge\b[^|]{0,20}\b\d+(\.\d+)?c\b", v, re.I):
            F("R4", cell(r, c), "edge written in cents; write points")
    # section index
    def find(pat):
        for r, row in enumerate(rows):
            if row and re.match(pat, row[0].strip(), re.I): return r
        return None
    p, b, t = find(r"PLAYS\b"), find(r"BELOW-GATE LIKES"), find(r"TOP 10")
    for name, idx in (("PLAYS", p), ("BELOW-GATE LIKES", b), ("TOP 10", t)):
        if idx is None: F("R6", "-", "section '%s' missing" % name)
    play_rows, like_rows, top_rows = [], [], []
    def block(start, end):
        out = []
        if start is None: return out
        for r in range(start + 1, end if end is not None else len(rows)):
            row = rows[r]
            if not any(x.strip() for x in row): break
            if row and re.match(r"(#|Rank|Market)$", row[0].strip()): continue
            out.append((r, row))
        return out
    play_rows = block(p, None if p is None else min([x for x in (b, t, len(rows)) if x is not None and x > p]))
    like_rows = block(b, None if b is None else min([x for x in (t, len(rows)) if x is not None and x > b]))
    top_rows = block(t, len(rows))
    # R6 top 10
    if t is not None:
        if len(top_rows) != 10: F("R6", cell(t, 0), "TOP 10 has %d lines, need 10" % len(top_rows))
    n_plays = sum(1 for r, row in play_rows if len(row) > 1 and re.match(r"PLAY\b", (row[-1] if row else "").strip(), re.I))
    if n_plays <= 2 and not any(OPENER in v for _, _, v in flat):
        F("R6", "-", "%d plays passed but the 'Only a couple of plays passed...' opener is missing" % n_plays)
    # R7 receipt count
    m = re.search(r"PLAYS POSTED:\s*(\d+)", a1)
    if m and int(m.group(1)) != n_plays:
        F("R7", "A1", "receipt says %s plays posted, PLAYS section has %d PLAY rows" % (m.group(1), n_plays))
    # R8 role check on prop legs
    role_lines = [v for _, _, v in flat if re.search(r"ROLE CHECK", v)]
    for name, rr in (("PLAYS", play_rows), ("LIKES", like_rows), ("TOP10", top_rows)):
        for r, row in rr:
            text = " ".join(row)
            bet = row[1] if name != "LIKES" and len(row) > 1 else row[0] if row else ""
            if PROP_WORDS.search(bet) and not re.search(r"\b(ML|spread|game total|1H total|team total)\b", bet, re.I):
                if "ROLE CHECK" in text: continue
                last = re.findall(r"[A-Z][A-Za-z'\.\-]+", re.sub(r"^\d+\s*", "", bet))
                nm = next((w for w in last if w not in ("UNDER", "OVER", "ML", "O", "U")), None)
                if not (nm and any(nm in rl for rl in role_lines)):
                    F("R8", cell(r, 0), "prop leg '%s' has no ROLE CHECK line" % bet[:60])
    # R9 units
    for r, c, v in flat:
        if re.search(r"\bstrong\b.*\bunits?\b|\bstrong tier\b", v, re.I) and "no 'strong'" not in v.lower():
            F("R9", cell(r, 0), "'strong' tier not allowed (0h.3)")
    if p is not None and p + 1 < len(rows):
        hdr = rows[p + 1]
        for ci, h in enumerate(hdr):
            if h.strip().lower().startswith("units"):
                for r, row in play_rows:
                    if ci < len(row) and re.fullmatch(r"\d+(\.\d+)?", row[ci].strip()) and float(row[ci]) > 1:
                        F("R9", cell(r, ci), "%su exceeds 1u max (0h.3)" % row[ci])
    # R10 likes wording
    for r, row in like_rows:
        if "I would do" not in " ".join(row) or "units if you approve" not in " ".join(row):
            F("R10", cell(r, 0), "below-gate like missing 'I would do X units if you approve'")
    return fails


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(2)
    now = None; age = 6.0
    if "--now" in args:
        i = args.index("--now"); now = parse_time(args[i + 1]); del args[i:i + 2]
    if "--max-price-age-h" in args:
        i = args.index("--max-price-age-h"); age = float(args[i + 1]); del args[i:i + 2]
    try:
        rows = load(args[0])
    except Exception as e:
        print("FAIL | input | cannot read %s: %s" % (args[0], e)); sys.exit(2)
    f = verify(rows, now, age)
    if f:
        print("FAIL (%d)" % len(f)); print("\n".join(f)); sys.exit(1)
    print("PASS")
