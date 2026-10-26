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
             'idrinkcoffee.com': 'iDrinkCoffee'}
BASE = "https://uplike7.github.io/espresso-deal-alerts/"

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

HUB_STYLE = ("body{background:#0e0b08;color:#f7f0e6;font-family:Inter,system-ui,sans-serif;"
             "line-height:1.6;margin:0}main{max-width:900px;margin:0 auto;padding:2rem 1.2rem}"
             "a{color:#e8a94e}h1{font-family:Fraunces,Georgia,serif}li{margin:.5rem 0}"
             ".note{color:#b9a894}")

HUB_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading} — Espresso Deal Alerts</title>
<meta name="description" content="{meta}">
<link rel="canonical" href="{base}{page}">
</head><body><main>
<p><a href="index.html">← Espresso Deal Alerts home</a></p>
<h1>{heading}</h1>
<p>{intro}</p>
<p class="note">{note}</p>
<ul>{items}</ul>
<p class="note">Prices update hourly; retailer prices prevail at checkout. Affiliate disclosure: links on this page may earn us a commission at no extra cost to you.</p>
</main></body></html>"""

def hub_pages(products):
    """Return {filename: html} for the four category hubs."""
    hubs = [
        ("machines.html", "Espresso machines",
         "Espresso machines we track hourly, with live prices and price history.",
         "Every espresso machine in the tracker, sorted by biggest saving. Prices are checked every hour against each model's own history.",
         "Type is derived from product titles (see build notes); machines are titles containing “machine” but not “grinder”.",
         [p for p in products if category(p["title"]) == "machine"]),
        ("grinders.html", "Espresso grinders",
         "Espresso grinders we track hourly, with live prices and price history.",
         "Every espresso grinder in the tracker, sorted by biggest saving. Prices are checked every hour against each model's own history.",
         "Type is derived from product titles; grinders are titles containing “grinder”.",
         [p for p in products if category(p["title"]) == "grinder"]),
        ("under-1000.html", "Espresso gear under $1,000",
         "Espresso machines and grinders under $1,000 (listed price), tracked hourly.",
         "Every tracked model with a listed price under $1,000, sorted by biggest saving.",
         "Bands use the LISTED price: USD items are in US dollars and iDrinkCoffee items are in Canadian dollars, shown per item. No currency conversion is applied.",
         [p for p in products if p["price"] < 1000]),
        ("under-500.html", "Espresso gear under $500",
         "Espresso machines and grinders under $500 (listed price), tracked hourly.",
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
            "</head>", f"<style>{HUB_STYLE}</style></head>")
    return out

def main():
    h = json.load(open('price-history.json'))
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
