#!/usr/bin/env python3
"""Build products.json (+ sitemap deal URLs) from price-history.json."""
import json, re, urllib.parse

RETAILERS = {'wholelattelove.com': 'Whole Latte Love', 'seattlecoffeegear.com': 'Seattle Coffee Gear',
             'clivecoffee.com': 'Clive Coffee', 'chriscoffee.com': "Chris' Coffee",
             'idrinkcoffee.com': 'iDrinkCoffee'}
BASE = "https://uplike7.github.io/espresso-deal-alerts/"

def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:80]

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

    # Refresh sitemap.xml at repo root: keep existing non-deals URLs, add all deal pages.
    try:
        sm = open('../sitemap.xml').read()
        urls = re.findall(r'<loc>(.*?)</loc>', sm)
        urls = [u for u in urls if '/deals/' not in u]
    except Exception:
        urls = [BASE]
    for p in products:
        urls.append(f"{BASE}deals/{p['slug']}.html")
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        out.append(f"<url><loc>{u}</loc></url>")
    out.append('</urlset>')
    open('../sitemap.xml', 'w').write("\n".join(out))
    print(f"products.json: {len(products)} products; sitemap: {len(urls)} URLs")

if __name__ == "__main__":
    main()
