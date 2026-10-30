#!/usr/bin/env python3
"""Build products.json (+ sitemap deal URLs + category hub pages) from price-history.json.

Also regenerated on every run: the per-model deal hubs under models/
(see MODELS) and black-friday-price-floors.html (static 2025 reference
data, see BF_FLOORS) — both are pipeline output, never hand-edited pages.

Sitemap: every URL carries <lastmod> set to the run date (UTC). Hub pages
are regenerated on every run from the same products list, so they never go
stale; make_products keeps pre-existing non-deal URLs and appends the deal
and hub URLs (deduplicated).

CATEGORY HEURISTIC: products.json has no category field, so hub membership
for machines/grinders is derived from title keywords (grinder checked first).
Price-band hubs use the LISTED price and say so on the page, because the
catalog mixes USD and CAD listings — a CAD price is never silently treated
as USD. Every price, count, and link on a hub page comes from products.json.
"""
import datetime, json, os, re, urllib.parse, html

RETAILERS = {'wholelattelove.com': 'Whole Latte Love', 'seattlecoffeegear.com': 'Seattle Coffee Gear',
             'clivecoffee.com': 'Clive Coffee', 'chriscoffee.com': "Chris' Coffee",
             'idrinkcoffee.com': 'iDrinkCoffee', 'amazon.com': 'Amazon'}
BASE = "https://espressodealalerts.com/"

def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:80]

def money(v, cur):
    return f"CAD {v:,.2f}" if cur == 'CAD' else f"${v:,.2f}"

def category(title):
    t = title.lower()
    if "grinder" in t:
        return "grinder"
    if "machine" in t:
        return "machine"
    return "other"

def fmt_stamp(iso):
    """'2026-10-26T14:50:03Z' -> 'Oct 26, 2026, 10:50 AM EDT' (America/New_York).

    Same formatting as build_pages.fmt_last_checked. Returns '' when the
    value is missing or unparseable, so callers drop the segment instead
    of showing a fabricated time.
    """
    if not iso:
        return ""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        local = dt.astimezone(ZoneInfo("America/New_York"))
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        h12 = local.hour % 12 or 12
        ampm = "AM" if local.hour < 12 else "PM"
        return (f"{months[local.month - 1]} {local.day}, {local.year}, "
                f"{h12}:{local.minute:02d} {ampm} {local.tzname()}")
    except Exception:
        return ""

def fresh_stamp(members):
    """'Prices last checked <time>' from the members' real last_checked
    values (the newest check among them), or '' if none carry one."""
    times = [p.get("last_checked") for p in members if p.get("last_checked")]
    if not times:
        return ""
    s = fmt_stamp(max(times))
    return f"Prices last checked {s}." if s else ""

GA4_SNIPPET = """<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-4L0H4N4G2T"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-4L0H4N4G2T');
</script>"""

HUB_STYLE = """
body{background:#faf5ec;color:#2b1d12;font-family:Inter,system-ui,sans-serif;line-height:1.6;margin:0}
.wrap{max-width:900px;margin:0 auto;padding:2rem 1.4rem}
a{color:#a85e17}
h1{font-family:Fraunces,Georgia,serif}
.hubnav{display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0 1.6rem}
.hubnav a{background:#fff;border:1px solid #e6dacb;color:#2b1d12;text-decoration:none;font-weight:600;font-size:.88rem;padding:.45rem 1rem;border-radius:99px}
.hubnav a:hover{border-color:#a85e17;color:#a85e17}
li{margin:.55rem 0}
li a{color:#2b1d12;text-decoration:none;font-weight:600}
li a:hover{color:#a85e17}
:focus-visible{outline:2px solid #a85e17;outline-offset:2px}
.note{color:#6b5b4c}
.fresh{color:#6b5b4c;font-size:.92rem}
.listing{border-top:1px solid #e6dacb;padding:.9rem 0}
.listing h2{font-size:1.15rem;margin:.2rem 0}
table.floors{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.95rem}
table.floors th,table.floors td{border:1px solid #e6dacb;padding:.5rem .7rem;text-align:left;vertical-align:top}
table.floors th{background:#f5e7d3}
"""

HUB_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{pagetitle}</title>
<meta name="description" content="{meta}">
<link rel="canonical" href="{base}{page}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<meta property="og:title" content="{pagetitle}">
<meta property="og:description" content="{meta}">
<meta property="og:type" content="website">
<meta property="og:url" content="{base}{page}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{pagetitle}">
<meta name="twitter:description" content="{meta}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
__GA4__
<script type="application/ld+json">{itemlist}</script>
</head><body><div class="wrap">
<p><a href="index.html">← Espresso Deal Alerts home</a></p>
<h1>{heading}</h1>
<nav class="hubnav">
<a href="index.html">Home</a><a href="machines.html">Machines</a><a href="grinders.html">Grinders</a>
<a href="models/">Model deals</a><a href="under-1000.html">Under $1,000</a><a href="under-500.html">Under $500</a>
<a href="refills.html">Refills</a><a href="deals/">All deals</a><a href="black-friday.html">Black Friday</a>
</nav>
<p>{intro}</p>
<p class="fresh">{fresh}</p>
<p class="note">{note}</p>
<ul>{items}</ul>
<p class="note">Prices update several times a day; retailer prices prevail at checkout. Affiliate disclosure: links on this page may earn us a commission at no extra cost to you.</p>
</div></body></html>"""

def hub_pages(products):
    """Return {filename: html} for the four category hubs.

    Spec: (filename, heading, pagetitle, meta, intro, note, members).
    pagetitle None = "{heading} — Espresso Deal Alerts" (the long-standing
    output). machines.html is the canonical "espresso machine deals" hub
    (ranking research, 2026-10-30): its title/H1/intro carry the exact
    phrase and it keeps the deal-first (biggest saving) ordering.
    """
    hubs = [
        ("machines.html", "Espresso machine deals",
         "Espresso Machine Deals — Live Price Tracker | Espresso Deal Alerts",
         "Espresso machine deals tracked live across specialty retailers and Amazon: current prices, biggest verified savings and the lowest tracked price for every machine we watch.",
         "Espresso machine deals, tracked live: every espresso machine in the tracker, sorted by biggest verified saving, with each price checked against the model's own tracked history several times a day.",
         "Type is derived from product titles (see build notes); machines are titles containing “machine” but not “grinder”.",
         [p for p in products if category(p["title"]) == "machine"]),
        ("grinders.html", "Espresso grinders", None,
         "Espresso grinders we track around the clock, with live prices and price history.",
         "Every espresso grinder in the tracker, sorted by biggest saving. Prices are checked several times a day against each model's own history.",
         "Type is derived from product titles; grinders are titles containing “grinder”.",
         [p for p in products if category(p["title"]) == "grinder"]),
        ("under-1000.html", "Espresso gear under $1,000", None,
         "Espresso machines and grinders under $1,000 (listed price), tracked around the clock.",
         "Every tracked model with a listed price under $1,000, sorted by biggest saving.",
         "Bands use the LISTED price: USD items are in US dollars and iDrinkCoffee items are in Canadian dollars, shown per item. No currency conversion is applied.",
         [p for p in products if p["price"] < 1000]),
        ("under-500.html", "Espresso gear under $500", None,
         "Espresso machines and grinders under $500 (listed price), tracked around the clock.",
         "Every tracked model with a listed price under $500, sorted by biggest saving.",
         "Bands use the LISTED price: USD items are in US dollars and iDrinkCoffee items are in Canadian dollars, shown per item. No currency conversion is applied.",
         [p for p in products if p["price"] < 500]),
    ]
    out = {}
    for filename, heading, pagetitle, meta, intro, note, members in hubs:
        members = sorted(members,
                         key=lambda p: -(((p["compare_at"] or p["price"]) - p["price"])))
        items = "".join(
            f'<li><a href="deals/{p["slug"]}.html">{html.escape(p["title"])}</a> — '
            f'{money(p["price"], p["currency"])} at {p["retailer"]}'
            f'{" — <strong>Sold out</strong>" if not p["available"] else ""}</li>' for p in members)
        itemlist = json.dumps({
            "@context": "https://schema.org", "@type": "ItemList",
            "name": f"{heading} — Espresso Deal Alerts",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1,
                 "url": f"{BASE}deals/{p['slug']}.html", "name": p["title"]}
                for i, p in enumerate(members)]})
        out[filename] = HUB_PAGE.format(
            heading=f"{heading} ({len(members)})",
            pagetitle=pagetitle or f"{heading} — Espresso Deal Alerts",
            meta=meta, base=BASE, page=filename,
            intro=intro, note=note, items=items, itemlist=itemlist,
            fresh=fresh_stamp(members))
        # Inline the shared style (keeps each hub a single self-contained file).
        out[filename] = out[filename].replace(
            "</head>", f"<style>{HUB_STYLE}</style></head>").replace("__GA4__", GA4_SNIPPET)
    return out

# ---------------------------------------------------------------------------
# Per-model deal hubs (models/<slug>.html + models/index.html).
#
# MODELS is a curated matcher table (same pattern as RETAILERS): titles
# are normalized (lowercased, ®/™ stripped), must match EVERY include
# regex and NO exclude regex. A model with no current listings generates
# no page. Selection (2026-10-30, from the ranking research): the
# multi-listing model families ranked by listing count + history depth,
# the deepest-history single-listing models, and the high-demand models
# the research names explicitly (Barista Express, Ninja Luxe, Philips
# 3200, DF54/DF64, Magnifica Start) — those are recent Amazon additions
# (2026-10-28), so their tracked history is still shallow; it deepens
# every tracker run. Every price, date and count on a hub comes from
# products.json / price-history.json. "Best" and "lowest" are computed
# PER CURRENCY: USD and CAD prices are never compared or converted.
# ---------------------------------------------------------------------------
MODELS = [
    # slug, display name, includes (all must match), excludes (none may)
    ("breville-barista-express", "Breville Barista Express", [r"barista express"], [r"impress"]),
    ("breville-bambino-plus", "Breville Bambino Plus", [r"bambino plus"], []),
    ("baratza-sette-270", "Baratza Sette 270 & 270Wi", [r"sette 270"], []),
    ("profitec-go", "Profitec GO", [r"profitec go"], []),
    ("mazzer-super-jolly-g", "Mazzer Super Jolly G", [r"super jolly"], []),
    ("mazzer-kony-g", "Mazzer Kony G", [r"mazzer kony"], []),
    ("lucca-a53-pro", "LUCCA A53 Pro", [r"a53 pro"], []),
    ("jura-j10-twin", "Jura J10 Twin", [r"j10"], []),
    ("ascaso-steel-duo", "Ascaso Steel Duo & Duo Plus", [r"steel duo"], []),
    ("delonghi-dedica", "De'Longhi Dedica", [r"dedica"], []),
    ("rocket-espresso-r58", "Rocket Espresso R58", [r"r58"], []),
    ("baratza-encore-esp", "Baratza Encore ESP", [r"encore esp"], [r"esp pro"]),
    ("la-spaziale-mini-vivaldi-ii", "La Spaziale Mini Vivaldi II", [r"vivaldi"], []),
    ("jura-z10", "Jura Z10", [r"z10"], []),
    ("jura-e8", "Jura E8", [r"jura e8"], []),
    ("mahlkonig-ek43", "Mahlkonig EK43", [r"ek43"], []),
    ("gaggia-classic-pro", "Gaggia Classic Pro", [r"classic pro"], []),
    ("breville-barista-touch", "Breville Barista Touch", [r"barista touch"], [r"impress"]),
    ("lelit-bianca-v3", "Lelit Bianca V3", [r"bianca"], []),
    ("ninja-luxe-cafe-premier", "Ninja Luxe Café Premier", [r"ninja", r"premier"], []),
    ("ninja-luxe-cafe-pro", "Ninja Luxe Café Pro", [r"ninja", r"pro 4-in-1"], []),
    ("philips-3200-lattego", "Philips 3200 LatteGo", [r"philips 3200"], []),
    ("miicoffee-df54", "MiiCoffee DF54", [r"df54"], []),
    ("df64-gen-2", "DF64 Gen 2", [r"df64"], []),
    ("delonghi-magnifica-start", "De'Longhi Magnifica Start", [r"magnifica start"], []),
]

MODEL_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} Deals — Current Price, Lowest Tracked &amp; Price History | Espresso Deal Alerts</title>
<meta name="description" content="{meta}">
<link rel="canonical" href="{base}models/{slug}.html">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<meta property="og:title" content="{name} Deals — Espresso Deal Alerts">
<meta property="og:description" content="{meta}">
<meta property="og:type" content="website">
<meta property="og:url" content="{base}models/{slug}.html">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{name} Deals — Espresso Deal Alerts">
<meta name="twitter:description" content="{meta}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
__GA4__
<script type="application/ld+json">{itemlist}</script>
</head><body><div class="wrap">
<p><a href="index.html">← All model deal hubs</a> · <a href="../index.html">Espresso Deal Alerts home</a></p>
<h1>{name} deals</h1>
<nav class="hubnav">
<a href="../index.html">Home</a><a href="../machines.html">Machines</a><a href="../grinders.html">Grinders</a>
<a href="index.html">Model deals</a><a href="../under-1000.html">Under $1,000</a><a href="../under-500.html">Under $500</a>
<a href="../refills.html">Refills</a><a href="../deals/">All deals</a><a href="../black-friday.html">Black Friday</a>
</nav>
<p>{summary}</p>
<p class="fresh">{fresh}</p>
{listings}
<p>Want a price-drop alert for the {name}? <a href="../index.html?alert={alert_q}">Get price-drop alerts</a> — we check prices several times a day and only write when there is a real drop.</p>
<p class="note">Prices update several times a day; retailer prices prevail at checkout. USD and Canadian-dollar listings are shown in their own currency — no conversion is applied. Affiliate disclosure: links on this page may earn us a commission at no extra cost to you.</p>
</div></body></html>"""

MODEL_INDEX = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espresso Deals by Model — Current &amp; Lowest Tracked Prices | Espresso Deal Alerts</title>
<meta name="description" content="Espresso machine deals by model: for each model we track, the current best price across retailers, the lowest price we have tracked, and its price history.">
<link rel="canonical" href="{base}models/">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<meta property="og:title" content="Espresso Deals by Model — Espresso Deal Alerts">
<meta property="og:description" content="Per-model deal hubs: current best price, lowest tracked price and price history for the espresso machines and grinders we track.">
<meta property="og:type" content="website">
<meta property="og:url" content="{base}models/">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="Espresso Deals by Model — Espresso Deal Alerts">
<meta name="twitter:description" content="Per-model deal hubs: current best price, lowest tracked price and price history.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
__GA4__
<script type="application/ld+json">{itemlist}</script>
</head><body><div class="wrap">
<p><a href="../index.html">← Espresso Deal Alerts home</a></p>
<h1>Espresso deals by model</h1>
<nav class="hubnav">
<a href="../index.html">Home</a><a href="../machines.html">Machines</a><a href="../grinders.html">Grinders</a>
<a href="../under-1000.html">Under $1,000</a><a href="../under-500.html">Under $500</a>
<a href="../refills.html">Refills</a><a href="../deals/">All deals</a><a href="../black-friday.html">Black Friday</a>
</nav>
<p>One hub per model: every tracked listing for that model across retailers, its current best price, the lowest price we have tracked, and its price history. Models are listed by number of tracked listings, then by depth of tracked history.</p>
<p class="fresh">{fresh}</p>
<ul>{items}</ul>
<p class="note">Prices update several times a day; retailer prices prevail at checkout. Affiliate disclosure: links on this page may earn us a commission at no extra cost to you.</p>
</div></body></html>"""

def _norm_title(t):
    return re.sub(r"[®™]", "", t.lower())

def model_members(products):
    """{model_slug: (display_name, [products])} for models with listings."""
    out = {}
    for slug, name, inc, exc in MODELS:
        inc_rx = [re.compile(r) for r in inc]
        exc_rx = [re.compile(r) for r in exc]
        members = []
        for p in products:
            t = _norm_title(p["title"])
            if all(rx.search(t) for rx in inc_rx) and not any(rx.search(t) for rx in exc_rx):
                members.append(p)
        if members:
            out[slug] = (name, members)
    return out

def lowest_point(snaps):
    """(price, 'YYYY-MM-DD') of the lowest snapshot, or (None, '')."""
    best = None
    for s in snaps or []:
        if s.get("price") is None:
            continue
        if best is None or s["price"] < best[0]:
            best = (s["price"], (s.get("t") or "")[:10])
    return best if best else (None, "")

def sparkline(snaps, cur):
    """Inline SVG polyline of tracked prices; '' with <2 usable points."""
    pts = [s["price"] for s in snaps or [] if s.get("price") is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    w, h, pad = 120, 32, 3
    span = (hi - lo) or 1.0
    coords = []
    for i, v in enumerate(pts):
        x = pad + (w - 2 * pad) * i / (len(pts) - 1)
        y = (h - pad) - (h - 2 * pad) * (v - lo) / span
        coords.append(f"{x:.1f},{y:.1f}")
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="Tracked price ranged from {money(lo, cur)} to {money(hi, cur)} '
            f'over {len(pts)} price checks"><polyline fill="none" stroke="#a85e17" '
            f'stroke-width="2" points="{" ".join(coords)}"/></svg>')

def _best_current(members, cur):
    """(product, any_available) with the lowest current price in currency
    cur — available listings preferred; None if the model has none in cur."""
    pool = [p for p in members if p["currency"] == cur]
    if not pool:
        return None
    avail = [p for p in pool if p["available"]]
    pick = min(avail or pool, key=lambda q: q["price"])
    return pick, bool(avail)

def _model_lowest(members, history, cur):
    """(price, date, product) of the model's lowest tracked snapshot in
    currency cur, across all its listings; None if no data."""
    best = None
    for p in members:
        if p["currency"] != cur:
            continue
        lp, ld = lowest_point(history.get(p["retailer_url"], {}).get("snapshots"))
        if lp is not None and (best is None or lp < best[0]):
            best = (lp, ld, p)
    return best

def model_hubs(products, history):
    """Return {'models/<file>': html} — per-model hubs + the index."""
    found = model_members(products)
    pages = {}
    index_rows = []
    for slug, (name, members) in found.items():
        members = sorted(members, key=lambda p: (not p["available"], p["price"]))
        summaries = []
        for cur in ("USD", "CAD"):
            bc = _best_current(members, cur)
            if bc:
                p, any_avail = bc
                s = (f"best current price {money(p['price'], cur)} at {p['retailer']}"
                     + ("" if any_avail else " (that listing is currently sold out — we keep tracking it)"))
                summaries.append(s)
            low = _model_lowest(members, history, cur)
            if low:
                lp, ld, p = low
                summaries.append(f"lowest tracked {money(lp, cur)} at {p['retailer']} on {ld}")
        summary = (f"{html.escape(name)} deals, tracked live across "
                   f"{len(members)} listing{'s' if len(members) != 1 else ''}: "
                   + "; ".join(summaries) + ".")
        blocks = []
        for p in members:
            snaps = history.get(p["retailer_url"], {}).get("snapshots", [])
            lp, ld = lowest_point(snaps)
            was = (f" (was {money(p['compare_at'], p['currency'])})"
                   if p["compare_at"] else "")
            sold = ("" if p["available"] else
                    ' — <strong style="color:#a33">Sold out</strong>')
            low_line = (f"Lowest tracked {money(lp, p['currency'])} on {ld} · "
                        if lp is not None else "")
            spark = sparkline(snaps, p["currency"])
            blocks.append(
                f'<div class="listing"><h2><a href="../deals/{p["slug"]}.html">'
                f'{html.escape(p["title"])}</a></h2>'
                f'<p><strong>{money(p["price"], p["currency"])}</strong>{was} '
                f'at {p["retailer"]}{sold}</p>'
                f'{("<p>" + spark + "</p>") if spark else ""}'
                f'<p class="note">{low_line}Tracked since {p["first_tracked"][:10]} · '
                f'{p["history_points"]} price checks · '
                f'<a href="../deals/{p["slug"]}.html">Full price history &amp; deal →</a></p></div>')
        itemlist = json.dumps({
            "@context": "https://schema.org", "@type": "ItemList",
            "name": f"{name} deals — Espresso Deal Alerts",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1,
                 "url": f"{BASE}deals/{p['slug']}.html", "name": p["title"]}
                for i, p in enumerate(members)]})
        meta = (f"{name} deals tracked live: current prices across retailers, "
                f"the lowest price we have tracked, and full price history.")
        page = MODEL_PAGE.format(
            name=html.escape(name), slug=slug, base=BASE, meta=html.escape(meta, quote=True),
            summary=summary, fresh=fresh_stamp(members), listings="".join(blocks),
            itemlist=itemlist, alert_q=urllib.parse.quote_plus(name))
        page = page.replace(
            "</head>", f"<style>{HUB_STYLE}</style></head>").replace("__GA4__", GA4_SNIPPET)
        pages[f"models/{slug}.html"] = page
        # Index row: best current price overall = the USD best when the
        # model has USD listings (the catalog is USD-first; CAD-only
        # models show their CAD best). Labelled with its currency either way.
        bc = _best_current(members, "USD") or _best_current(members, "CAD")
        pts = sum(p["history_points"] for p in members)
        best_txt = ""
        if bc:
            p, _ = bc
            best_txt = f" · best current {money(p['price'], p['currency'])} at {p['retailer']}"
        index_rows.append((-len(members), -pts, name,
                           f'<li><a href="{slug}.html">{html.escape(name)} deals</a> — '
                           f'{len(members)} listing{"s" if len(members) != 1 else ""}{best_txt}</li>'))
    index_rows.sort()
    itemlist = json.dumps({
        "@context": "https://schema.org", "@type": "ItemList",
        "name": "Espresso deals by model — Espresso Deal Alerts",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1,
             "url": f"{BASE}models/{slug}.html", "name": f"{name} deals"}
            for i, (slug, (name, _)) in enumerate(found.items())]})
    page = MODEL_INDEX.format(
        base=BASE, fresh=fresh_stamp(products),
        items="".join(r[3] for r in index_rows), itemlist=itemlist)
    page = page.replace(
        "</head>", f"<style>{HUB_STYLE}</style></head>").replace("__GA4__", GA4_SNIPPET)
    pages["models/index.html"] = page
    return pages

# ---------------------------------------------------------------------------
# Black Friday reference-floor page (black-friday-price-floors.html).
#
# STATIC RESEARCH DATA, not tracker output: the lowest prices popular
# models actually reached around Black Friday 2025, from public deal
# records researched 2026-10 (source file Black-Friday-2026-Deal-Floors
# in the goal workspace). The page labels itself as a 2025 reference
# everywhere it matters; live prices live on the tracker pages. Kept in
# code so the page is regenerated — never hand-edited — with the hubs.
# ---------------------------------------------------------------------------
BF_FLOORS = [
    # model, typical list price, Black Friday 2025 floor, other notable lows, "a deal is" note
    ("Breville Barista Express (BES870)", "$699.95–$750", "$500 (Nov 26–Dec 3)",
     "$500 also at Oct 2025 Prime Big Deal Days, Prime Day Jun 2026, and again from Sep 27, 2026; one $491 dip in Dec 2024",
     "At or below $500"),
    ("Breville Bambino Plus", "~$500", "$400 — as low as it has gone since 2019",
     "Still $400 in late Oct 2026", "At or below $400"),
    ("Breville Bambino (BES450)", "$299.95", "No Black Friday floor found in sources",
     "$249.95 at Amazon from Sep 28, 2026; $50 off at Target", "$230 or below would be a genuine new low"),
    ("Ninja Luxe Café Premier (ES601)", "$599.99", "$499 (from Nov 23)",
     "$499 also at Oct 2025 Prime Big Deal Days and Prime Day Jun 2026; one $480 dip on Jun 4, 2026. A $249.99 refurbished offer at Woot (Oct 2026) is a separate category",
     "At or below $499"),
    ("Ninja Luxe Café Pro (ES701)", "$749", "$600",
     "$600 also at Prime Day 2026 and in an Oct 2026 sale", "At or below $600"),
    ("Ninja Luxe Café Mini Plus (ES351)", "$499.99", "Launched May 2026 — no 2025 Black Friday",
     "$450 in an Oct 2026 sale; ~$450 expected in Nov 2026", "At or below $450"),
    ("Philips 3200 LatteGo", "$799.99", "$499.99",
     "$450 from Nov 28 into mid-Dec 2025, then $398 from Dec 18 and ~$400 in early Jan — the best prices came AFTER Black Friday",
     "At or below $450; the floor slid into December"),
    ("Baratza Encore ESP", "~$199", "$159", "—", "At or below $159"),
    ("Gaggia Classic Pro", "$499", "$453.53 at Amazon (~9% off — Gaggia discounts are shallow)",
     "$499 at Whole Latte Love (E24) in late Oct 2026", "At or below $455"),
    ("De'Longhi Dedica", "$250", "No Black Friday floor found in sources",
     "Sat at $150 from mid-Jun to Oct 8, 2026 — do not pay $250", "At or below $160"),
    ("De'Longhi Magnifica Evo", "~$650", "$550", "$500 at Prime Day Jun 2026", "At or below $550"),
]

BF_FLOOR_SOURCES = [
    "https://blackfridaydeals.dev/blog/best-time-to-buy-espresso-machine-black-friday-vs-prime-day",
    "https://thecoffeefolk.com/black-friday-cyber-monday-coffee-deals/",
    "https://www.coffeelabadvisor.com/guides/breville-bambino-black-friday-deals",
    "https://dailybrewfix.com/amazon-black-friday-coffee-gears/",
    "https://www.purewow.com/home/black-friday-coffee-maker-deals",
    "https://www.tastingtable.com/2029545/best-black-friday-cafe-quality-espresso-machine-deals/",
    "https://www.dealnews.com/products/Ninja/Refurb-Ninja-Luxe-Caf-Premier-Series-Espresso-Machine/546691.html",
    "https://pricehistory.app/p/breville-bambino-espresso-machine-nSz2Mow8",
]

FLOOR_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Black Friday Espresso Price Floors — What Top Models Actually Sold For in 2025 | Espresso Deal Alerts</title>
<meta name="description" content="Reference floors from Black Friday 2025: the lowest prices popular espresso machines and grinders actually reached, from public deal records — so you can judge 2026 'sales' against real history.">
<link rel="canonical" href="{base}black-friday-price-floors.html">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<meta property="og:title" content="Black Friday Espresso Price Floors (2025 Reference) — Espresso Deal Alerts">
<meta property="og:description" content="The lowest prices top espresso models actually reached around Black Friday 2025, from public deal records. A reference for judging 2026 sales.">
<meta property="og:type" content="website">
<meta property="og:url" content="{base}black-friday-price-floors.html">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="Black Friday Espresso Price Floors (2025 Reference) — Espresso Deal Alerts">
<meta name="twitter:description" content="The lowest prices top espresso models actually reached around Black Friday 2025, from public deal records.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
__GA4__
</head><body><div class="wrap">
<p><a href="index.html">← Espresso Deal Alerts home</a> · <a href="black-friday.html">Black Friday 2026 tracker</a></p>
<h1>Black Friday price floors: what espresso machines actually sold for in 2025</h1>
<nav class="hubnav">
<a href="index.html">Home</a><a href="machines.html">Machines</a><a href="grinders.html">Grinders</a>
<a href="models/">Model deals</a><a href="under-1000.html">Under $1,000</a><a href="under-500.html">Under $500</a>
<a href="refills.html">Refills</a><a href="deals/">All deals</a><a href="black-friday.html">Black Friday</a>
</nav>
<p><strong>What this page is:</strong> a reference table of the lowest prices popular espresso machines and grinders actually reached around Black Friday 2025, compiled from public deal records (researched October 2026). <strong>What it is not:</strong> live prices, or a promise any retailer will repeat them. For live 2026 prices, use the <a href="black-friday.html">Black Friday tracker</a> and the <a href="models/">per-model deal hubs</a>, which are checked against our own tracked price history several times a day. All prices USD.</p>
<table class="floors">
<tr><th scope="col">Model</th><th scope="col">Typical list price</th><th scope="col">Black Friday 2025 floor</th><th scope="col">Other notable lows (2025–26)</th><th scope="col">What we call a deal</th></tr>
{rows}
</table>
<h2>How to use these floors</h2>
<ul>
<li>Breville runs the same $500-class sales three or four times a year. November 2026 may not beat them — the win is catching the <em>return</em> to the floor if the price lifts first.</li>
<li>Ninja and Philips lows land at the sale events themselves, not between them.</li>
<li>The Philips 3200's floor kept dropping after Thanksgiving 2025 ($450, then $398 in mid-December) — a Black Friday price is not always the floor, and our "lowest tracked" labels never freeze at the event price.</li>
<li>We never tag a deal "lowest" at full list price. A sale price above last year's floor is usually worth waiting on.</li>
</ul>
<h2>Sources</h2>
<p class="note">Compiled October 2026 from public deal coverage and price records, including:</p>
<ul>{sources}</ul>
<p class="note">Affiliate disclosure: links on this site may earn us a commission at no extra cost to you. Retailer prices prevail at checkout.</p>
</div></body></html>"""

def floor_page():
    """The Black Friday 2025 reference-floor page (static research data)."""
    rows = "".join(
        f"<tr><td>{html.escape(m)}</td><td>{html.escape(listp)}</td>"
        f"<td><strong>{html.escape(floor)}</strong></td><td>{html.escape(other)}</td>"
        f"<td>{html.escape(deal)}</td></tr>"
        for m, listp, floor, other, deal in BF_FLOORS)
    sources = "".join(
        f'<li><a href="{u}" rel="noopener">{html.escape(u.split("//", 1)[-1])}</a></li>'
        for u in BF_FLOOR_SOURCES)
    page = FLOOR_PAGE.format(base=BASE, rows=rows, sources=sources)
    return page.replace(
        "</head>", f"<style>{HUB_STYLE}</style></head>").replace("__GA4__", GA4_SNIPPET)

def build_drops(history, products, window_days=7, limit=12):
    """Latest genuine price drop per product, from price-history.json.

    For each product, scan snapshots newest-first and take the most recent
    index i>0 where price[i] < price[i-1]. Emit only if that drop is within
    ``window_days`` of the newest snapshot anywhere in the file, so stale
    drops age out as history grows. One entry per product, newest first,
    capped at ``limit``. Missing/empty history yields an empty drops list —
    never a fabricated entry. Slugs come from the final products list (after
    collision suffixing), so they always match real deal pages.
    """
    by_url = {p['retailer_url']: p for p in products}
    latest = None
    for e in history.values():
        for s in e.get('snapshots') or []:
            t = s.get('t')
            if t and (latest is None or t > latest):
                latest = t
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
    if latest is None:
        return {"generated_at": generated_at, "drops": []}
    try:
        cutoff = datetime.datetime.fromisoformat(latest.replace('Z', '+00:00')) \
            - datetime.timedelta(days=window_days)
    except Exception:
        cutoff = None
    drops = []
    for url, e in history.items():
        p = by_url.get(url)
        if p is None:
            continue
        snaps = e.get('snapshots') or []
        drop = None
        for i in range(len(snaps) - 1, 0, -1):
            cur, prev = snaps[i], snaps[i - 1]
            if cur.get('price') is None or prev.get('price') is None:
                continue
            if cur['price'] < prev['price']:
                drop = (cur, prev)
                break
        if drop is None:
            continue
        cur, prev = drop
        t = cur.get('t') or ''
        if cutoff is not None and t:
            try:
                if datetime.datetime.fromisoformat(t.replace('Z', '+00:00')) < cutoff:
                    continue
            except Exception:
                pass
        drops.append({'slug': p['slug'], 'title': p['title'], 'retailer': p['retailer'],
                      'retailer_url': url, 'old_price': prev['price'], 'new_price': cur['price'],
                      'currency': p['currency'], 'dropped_on': t[:10], '_t': t})
    drops.sort(key=lambda d: d['_t'], reverse=True)
    for d in drops:
        del d['_t']
    return {"generated_at": generated_at, "drops": drops[:limit]}

def main():
    h = json.load(open('price-history.json'))
    # Product photos: retailer URL -> featured image URL, fetched from the
    # retailers' own product endpoints. Absent file = no photos, never invented.
    try:
        img_map = json.load(open('product-images.json'))
    except Exception:
        img_map = {}
    # Manual overrides for retailers with no product endpoint to auto-fetch
    # (e.g. Amazon): each record in image-overrides.json is the main image
    # from that product's own listing page, with provenance. Overrides win.
    try:
        for rec in json.load(open('image-overrides.json')).get('overrides', []):
            if rec.get('url') and rec.get('image_url'):
                img_map[rec['url']] = rec['image_url']
    except Exception:
        pass
    products = []
    for url, e in h.items():
        domain = url.split('/')[2].replace('www.', '')
        retailer = RETAILERS.get(domain, domain)
        snap = e['snapshots'][-1]
        q = urllib.parse.quote_plus(e['title'])
        products.append({
            'title': e['title'], 'retailer': retailer, 'retailer_url': url,
            'slug': slugify(e['title']),
            'price': snap['price'], 'compare_at': snap['compare_at'], 'available': snap['available'],
            'currency': 'CAD' if retailer == 'iDrinkCoffee' else 'USD',
            'amazon_search': f"https://www.amazon.com/s?k={q}",
            'ebay_search': f"https://www.ebay.com/sch/i.html?_nkw={q}",
            'first_tracked': e['snapshots'][0]['t'], 'last_checked': snap['t'],
            'lowest_tracked': min(s['price'] for s in e['snapshots']),
            'history_points': len(e['snapshots']),
            **({'image_url': img_map[url]} if url in img_map else {}),
        })
    products.sort(key=lambda p: -(((p['compare_at'] or p['price']) - p['price'])))
    # Collision-safe slugs: when 2+ retailers carry the same model, suffix each
    # page's slug with its retailer so every product gets its own page.
    base_count = {}
    for p in products:
        base_count[p['slug']] = base_count.get(p['slug'], 0) + 1
    for p in products:
        if base_count[p['slug']] > 1:
            p['slug'] = f"{p['slug']}-{slugify(p['retailer'])}"
    json.dump(products, open('products.json', 'w'), indent=1)

    # Category hub pages at the repo root (regenerated every run).
    hubs = hub_pages(products)
    for filename, page in hubs.items():
        open(f'../{filename}', 'w').write(page)

    # Per-model deal hubs under models/ (regenerated every run).
    models = model_hubs(products, h)
    os.makedirs('../models', exist_ok=True)
    for rel, page in models.items():
        open(f'../{rel}', 'w').write(page)

    # Black Friday 2025 reference-floor page (regenerated every run from
    # the static research data in BF_FLOORS, so it never drifts by hand).
    open('../black-friday-price-floors.html', 'w').write(floor_page())

    # Latest price drops feed for the homepage (drops.json at repo root).
    # NOTE: the hourly workflow must git-add drops.json for it to deploy;
    # until then the homepage drops section stays hidden by design.
    drops = build_drops(h, products)
    json.dump(drops, open('../drops.json', 'w'), indent=1)

    # Refresh sitemap.xml at repo root: keep existing non-deals URLs, add all
    # deal pages + hub pages, every URL stamped with the run date.
    run_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    try:
        sm = open('../sitemap.xml').read()
        urls = re.findall(r'<loc>(.*?)</loc>', sm)
        urls = [u for u in urls if '/deals/' not in u]
    except Exception:
        urls = [BASE]
    seen = set()
    ordered = []
    for u in urls + [f"{BASE}deals/{p['slug']}.html" for p in products] \
            + [f"{BASE}{f}" for f in hubs] + [f"{BASE}{rel}" for rel in models] \
            + [f"{BASE}black-friday-price-floors.html"] + [f"{BASE}deals/"]:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in ordered:
        out.append(f"<url><loc>{u}</loc><lastmod>{run_date}</lastmod></url>")
    out.append('</urlset>')
    open('../sitemap.xml', 'w').write("\n".join(out))
    print(f"products.json: {len(products)} products; sitemap: {len(ordered)} URLs; "
          f"hubs: {', '.join(f'{f} ({p})' for f, p in [(k, v.count('<li>')) for k, v in hubs.items()])}; "
          f"model hubs: {len(models) - 1} + index")

if __name__ == "__main__":
    main()
