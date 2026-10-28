#!/usr/bin/env python3
"""Build products.json (+ sitemap deal URLs + category hub pages) from price-history.json.

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
import datetime, json, re, urllib.parse, html

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
"""

HUB_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading} — Espresso Deal Alerts</title>
<meta name="description" content="{meta}">
<link rel="canonical" href="{base}{page}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<meta property="og:title" content="{heading} — Espresso Deal Alerts">
<meta property="og:description" content="{meta}">
<meta property="og:type" content="website">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
__GA4__
</head><body><div class="wrap">
<p><a href="index.html">← Espresso Deal Alerts home</a></p>
<h1>{heading}</h1>
<nav class="hubnav">
<a href="index.html">Home</a><a href="machines.html">Machines</a><a href="grinders.html">Grinders</a>
<a href="under-1000.html">Under $1,000</a><a href="under-500.html">Under $500</a>
</nav>
<p>{intro}</p>
<p class="note">{note}</p>
<ul>{items}</ul>
<p class="note">Prices update several times a day; retailer prices prevail at checkout. Affiliate disclosure: links on this page may earn us a commission at no extra cost to you.</p>
</div></body></html>"""

def hub_pages(products):
    """Return {filename: html} for the four category hubs."""
    hubs = [
        ("machines.html", "Espresso machines",
         "Espresso machines we track around the clock, with live prices and price history.",
         "Every espresso machine in the tracker, sorted by biggest saving. Prices are checked several times a day against each model's own history.",
         "Type is derived from product titles (see build notes); machines are titles containing “machine” but not “grinder”.",
         [p for p in products if category(p["title"]) == "machine"]),
        ("grinders.html", "Espresso grinders",
         "Espresso grinders we track around the clock, with live prices and price history.",
         "Every espresso grinder in the tracker, sorted by biggest saving. Prices are checked several times a day against each model's own history.",
         "Type is derived from product titles; grinders are titles containing “grinder”.",
         [p for p in products if category(p["title"]) == "grinder"]),
        ("under-1000.html", "Espresso gear under $1,000",
         "Espresso machines and grinders under $1,000 (listed price), tracked around the clock.",
         "Every tracked model with a listed price under $1,000, sorted by biggest saving.",
         "Bands use the LISTED price: USD items are in US dollars and iDrinkCoffee items are in Canadian dollars, shown per item. No currency conversion is applied.",
         [p for p in products if p["price"] < 1000]),
        ("under-500.html", "Espresso gear under $500",
         "Espresso machines and grinders under $500 (listed price), tracked around the clock.",
         "Every tracked model with a listed price under $500, sorted by biggest saving.",
         "Bands use the LISTED price: USD items are in US dollars and iDrinkCoffee items are in Canadian dollars, shown per item. No currency conversion is applied.",
         [p for p in products if p["price"] < 500]),
    ]
    out = {}
    for filename, heading, meta, intro, note, members in hubs:
        members = sorted(members,
                         key=lambda p: -(((p["compare_at"] or p["price"]) - p["price"])))
        items = "".join(
            f'<li><a href="deals/{p["slug"]}.html">{html.escape(p["title"])}</a> — '
            f'{money(p["price"], p["currency"])} at {p["retailer"]}'
            f'{" — <strong>Sold out</strong>" if not p["available"] else ""}</li>' for p in members)
        out[filename] = HUB_PAGE.format(
            heading=f"{heading} ({len(members)})", meta=meta, base=BASE, page=filename,
            intro=intro, note=note, items=items)
        # Inline the shared style (keeps each hub a single self-contained file).
        out[filename] = out[filename].replace(
            "</head>", f"<style>{HUB_STYLE}</style></head>").replace("__GA4__", GA4_SNIPPET)
    return out

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
            + [f"{BASE}{f}" for f in hubs]:
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
          f"hubs: {', '.join(f'{f} ({p})' for f, p in [(k, v.count('<li>')) for k, v in hubs.items()])}")

if __name__ == "__main__":
    main()
