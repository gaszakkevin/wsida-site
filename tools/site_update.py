#!/usr/bin/env python3
"""WSIDA site updater (idempotent, additive, stdlib only).

Keeps releases.json (append-only list of public releases) and renders the
"New releases" section of index.html between RELEASES markers. Nothing else in
index.html is touched. Parent-facing links only; no forms, no trackers, no
third-party images (COPPA-friendly), never any home address.

Commands:
  discover            add new PUBLIC YouTube uploads (channel RSS) + live Gumroad products
  add --type T --title X --url U [--date YYYY-MM-DD] [--note N] [--trusted]
                      T = video | short | coloring | book
  from-issue          parse ISSUE_BODY env (lines "type: / title: / url: / date: / note:")
  render              re-verify links and rewrite the RELEASES block in index.html
"""
import argparse, datetime as dt, html, json, os, re, sys, urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "releases.json")
INDEX = os.path.join(ROOT, "index.html")
CHANNEL_ID = "UCWP8iVWyVEK6f1REAgH0RNg"
GUMROAD_STORE = "https://wsidadreams.gumroad.com/"
CUTOFF = "2026-11-11"          # backfill start; older uploads are already featured above
TYPES = ("video", "short", "coloring", "book")
ALLOWED = re.compile(r"^https://(youtu\.be/[\w-]{11}|www\.youtube\.com/(watch\?v=|shorts/)[\w-]{11}"
                     r"|wsidadreams\.gumroad\.com/l/[\w-]+|www\.amazon\.com/dp/[A-Z0-9]{10})$")
START, END = "<!-- RELEASES:START -->", "<!-- RELEASES:END -->"
UA = {"User-Agent": "Mozilla/5.0 (WSIDA site updater; +https://whatshouldidreamabout.com)"}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None

def fetch(url, follow=True, timeout=20):
    """Return (status, body). status 0 = network error."""
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(urllib.request.Request(url, headers=UA), timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""

def yt_id(url):
    m = re.search(r"(?:youtu\.be/|v=|shorts/)([\w-]{11})", url); return m.group(1) if m else None

def check(entry):
    """True = live, False = definitively not live, None = unknown (network/bot wall)."""
    url = entry["url"]
    if not ALLOWED.match(url): return False
    if "youtu" in url:
        s, _ = fetch("https://www.youtube.com/oembed?format=json&url=" +
                     urllib.parse.quote("https://www.youtube.com/watch?v=" + yt_id(url)))
        return True if s == 200 else (False if s in (400, 401, 403, 404) else None)
    if "gumroad" in url:
        s, _ = fetch(url)
        return True if s == 200 else (False if s in (404, 410) else None)
    if "amazon" in url:
        s, body = fetch(url)
        if s == 200 and url.rsplit("/", 1)[1] in body: return True
        return False if s in (404, 410) else None
    return False

def load():
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f: return json.load(f)
    return {"entries": []}

def save(d):
    d["entries"].sort(key=lambda e: (e.get("date", ""), e["type"] != "short", e["url"]), reverse=True)
    with open(DATA, "w", encoding="utf-8") as f: json.dump(d, f, indent=2, ensure_ascii=False); f.write("\n")

def canon(url):
    i = yt_id(url) if "youtu" in url else None
    return f"https://youtu.be/{i}" if i else url.split("?")[0].rstrip("/")

def add(d, typ, title, url, date=None, note="", trusted=False, source="manual"):
    url = canon(url)
    if typ not in TYPES: raise SystemExit(f"bad type {typ}")
    if not ALLOWED.match(url): raise SystemExit(f"URL not allowed: {url}")
    if any(e["url"] == url for e in d["entries"]):
        print("exists:", url); return False
    e = {"type": typ, "title": title.strip()[:140], "url": url,
         "date": date or dt.date.today().isoformat(), "note": note.strip()[:140], "source": source}
    ok = check(e)
    if ok is None and trusted and "amazon" in url: ok = True   # Amazon walls bots; owner-verified
    if ok is not True:
        print(f"NOT live ({ok}), skipped:", url); return False
    e["live"] = True
    d["entries"].append(e); print("added:", typ, url); return True

def discover(d):
    s, x = fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}")
    if s == 200:
        for ent in re.findall(r"<entry>(.*?)</entry>", x, re.S):
            vid = re.search(r"<yt:videoId>([\w-]{11})<", ent).group(1)
            date = re.search(r"<published>([\d-]{10})", ent).group(1)
            title = html.unescape(re.search(r"<title>(.*?)</title>", ent, re.S).group(1))
            if date < CUTOFF or any(e["url"] == f"https://youtu.be/{vid}" for e in d["entries"]): continue
            ss, _ = fetch(f"https://www.youtube.com/shorts/{vid}", follow=False)
            typ = "short" if ss == 200 else ("video" if ss in (301, 302, 303) else
                  ("short" if "|" not in title else "video"))
            add(d, typ, title, f"https://youtu.be/{vid}", date, source="youtube-rss")
    else:
        print("RSS unavailable:", s)
    s, h = fetch(GUMROAD_STORE)
    m = re.search(r'data-page="([^"]+)"', h or "")
    if s == 200 and m:
        page = json.loads(html.unescape(m.group(1)))
        found = []
        def walk(o):
            if isinstance(o, dict):
                if o.get("permalink") and o.get("name"): found.append((o["name"], o["permalink"]))
                for v in o.values(): walk(v)
            elif isinstance(o, list):
                for v in o: walk(v)
        walk(page)
        for name, perm in dict.fromkeys(found):
            url = f"https://wsidadreams.gumroad.com/l/{perm}"
            if any(e["url"] == url for e in d["entries"]): continue
            typ = "book" if re.search(r"journal|story|book", name, re.I) else "coloring"
            add(d, typ, name, url, source="gumroad")
    else:
        print("Gumroad store unavailable:", s)

def short_title(t):
    t = re.sub(r"\s*\|.*$", "", t).strip()
    return t

def fmt_date(s):
    try: d = dt.date.fromisoformat(s); return d.strftime("%b ") + str(d.day)
    except Exception: return ""

def card(e, badge, cta):
    t = html.escape(short_title(e["title"]))
    note = f'<p>{html.escape(e["note"])}</p>' if e.get("note") else ""
    dated = f' • {fmt_date(e["date"])}' if e["type"] in ("video", "short") else ""
    return (f'    <a class="card rel" href="{html.escape(e["url"])}" target="_blank" rel="noopener">\n'
            f'      <div class="pad"><span class="badge">{badge}{dated}</span>'
            f'<h3>{t}</h3>{note}<p class="go">{cta} →</p></div>\n    </a>')

def render(d, reverify=True):
    if reverify:
        for e in d["entries"]:
            if "amazon" in e["url"]: continue
            r = check(e)
            if r is not None: e["live"] = r
    live = [e for e in d["entries"] if e.get("live")]
    vids = [e for e in live if e["type"] in ("video", "short")][:16]
    col = [e for e in live if e["type"] == "coloring"]
    books = [e for e in live if e["type"] == "book"]
    parts = [START,
             '<style>#new .rel .pad{padding:16px 18px}#new .rel .go{color:var(--glow);font-weight:600;margin-top:6px}'
             '#new .shelf{margin:26px 0 12px}</style>',
             '<section id="new">',
             '  <h2>New releases ✨</h2>',
             '  <p class="sub">The newest bedtime videos, coloring packs and books — added here as soon as they go live.</p>']
    def shelf(title, items, fn):
        if not items: return
        parts.append(f'  <h3 class="shelf">{title}</h3>\n  <div class="grid">')
        parts.extend(fn(e) for e in items)
        parts.append('  </div>')
    shelf("Latest sleep videos &amp; Shorts", vids,
          lambda e: card(e, "Full video" if e["type"] == "video" else "Short", "Watch on YouTube"))
    shelf("Coloring packs &amp; printables", col,
          lambda e: card(e, "Gumroad", "Get it on Gumroad"))
    shelf("Books", books,
          lambda e: card(e, "Amazon" if "amazon" in e["url"] else "Gumroad",
                         "See it on Amazon" if "amazon" in e["url"] else "Get it on Gumroad"))
    parts += ['</section>', END]
    block = "\n".join(parts)
    with open(INDEX, encoding="utf-8") as f: h = f.read()
    if START in h and END in h:
        new = h[:h.index(START)] + block + h[h.index(END) + len(END):]
    else:
        anchor = '<section id="dreams">'
        if anchor not in h: raise SystemExit("anchor missing; refusing to edit")
        new = h.replace(anchor, block + "\n\n" + anchor, 1)
    if new != h:
        with open(INDEX, "w", encoding="utf-8") as f: f.write(new)
        print("index.html updated")
    else:
        print("index.html unchanged")

def from_issue(d):
    body = os.environ.get("ISSUE_BODY", "")
    kv = {k.strip().lower(): v.strip() for k, v in re.findall(r"^\s*(type|title|url|date|note)\s*:\s*(.+)$", body, re.M | re.I)}
    if not all(k in kv for k in ("type", "title", "url")): raise SystemExit("issue needs type/title/url")
    add(d, kv["type"].lower(), kv["title"], kv["url"], kv.get("date"), kv.get("note", ""),
        trusted=True, source="owner-issue")

def main():
    p = argparse.ArgumentParser(); sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("discover"); sp.add_parser("from-issue")
    r = sp.add_parser("render"); r.add_argument("--no-reverify", action="store_true")
    a = sp.add_parser("add")
    for k in ("type", "title", "url"): a.add_argument("--" + k, required=True)
    a.add_argument("--date"); a.add_argument("--note", default=""); a.add_argument("--trusted", action="store_true")
    o = p.parse_args(); d = load()
    if o.cmd == "discover": discover(d)
    elif o.cmd == "from-issue": from_issue(d)
    elif o.cmd == "add": add(d, o.type, o.title, o.url, o.date, o.note, o.trusted)
    elif o.cmd == "render": render(d, not o.no_reverify)
    save(d)

if __name__ == "__main__":
    main()
