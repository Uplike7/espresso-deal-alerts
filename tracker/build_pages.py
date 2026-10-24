#!/usr/bin/env python3
"""Build one SEO page per tracked product from products.json.

Each page: unique title/meta targeting '<Model> price / deals', live price +
compare-at + tracked-low, price-history summary, Product/Offer JSON-LD,
FAQ block (AEO), and outbound links. Affiliate params are injected from
config at ship time — pass --amazon-tag and --ebay-campid once ownership is
VERIFIED; pages built without them carry clean links (no wrong-tag leakage).
"""
import argparse, html, json, re

def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")

def money(v, cur):
    return f"{'CAD ' if cur=='CAD' else '$'}{v:,.2f}" if cur == 'CAD' else f"${v:,.2f}"

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} Price, Deals &amp; Price History — Espresso Deal Alerts</title>
<meta name="description" content="Live price for the {title}: {price} at {retailer}. Tracked hourly against price history — lowest tracked {lowest}. Verified deals, no fake sales.">
<link rel="canonical" href="https://uplike7.github.io/espresso-deal-alerts/deals/{slug}.html">
<script type="application/ld+json">{schema}</script>
</head><body>
<main style="max-width:760px;margin:0 auto;padding:2rem 1.2rem;font-family:Georgia,serif;line-height:1.6">
<p><a href="../index.html">← All espresso deals</a></p>
<h1>{title}</h1>
<p><strong>Current price: {price}</strong>{was} at {retailer}</p>
<p>Lowest price we've tracked: <strong>{lowest}</strong> · Price checked hourly · Last checked {last}</p>
<p><a href="{retailer_url}" rel="sponsored nofollow">View deal at {retailer} →</a></p>
<p>Also check: <a href="{amazon}" rel="sponsored nofollow">Amazon</a> · <a href="{ebay}" rel="sponsored nofollow">eBay</a></p>
<h2>Is this a good price?</h2>
<p>{verdict}</p>
<h2>Questions</h2>
<h3>What is the lowest price the {title} has been?</h3>
<p>Since we began tracking it, the lowest recorded price is {lowest} (current retailer listing: {price} at {retailer}).</p>
<h3>How often does the price change?</h3>
<p>We check this product's price every hour across specialty retailers and log every change to our price history.</p>
<p style="font-size:.9em;color:#666">Affiliate disclosure: links on this page may earn us a commission at no extra cost to you. Prices update hourly; retailer prices prevail at checkout.</p>
</main></body></html>"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", default="products.json")
    ap.add_argument("--out", default="deals")
    ap.add_argument("--amazon-tag", default="")
    ap.add_argument("--ebay-campid", default="")
    args = ap.parse_args()
    import os
    os.makedirs(args.out, exist_ok=True)
    for stale in os.listdir(args.out):
        if stale.endswith(".html"):
            os.remove(os.path.join(args.out, stale))
    products = json.load(open(args.products))
    n = 0
    for p in products:
        slug = p.get("slug") or slugify(p["title"])[:80]
        amazon = p["amazon_search"] + (f"&tag={args.amazon_tag}" if args.amazon_tag else "")
        ebay = p["ebay_search"] + (f"&campid={args.ebay_campid}" if args.ebay_campid else "")
        price = money(p["price"], p["currency"]); lowest = money(p["lowest_tracked"], p["currency"])
        was = f" (was {money(p['compare_at'], p['currency'])})" if p["compare_at"] else ""
        saving = (p["compare_at"] - p["price"]) if p["compare_at"] else 0
        verdict = (f"At {price} this is {money(saving, p['currency'])} below its compare-at price"
                   f"{' and at the lowest price we have tracked' if abs(p['price']-p['lowest_tracked'])<0.01 else ''}. "
                   "Our verdicts follow the tracked data, not retailer marketing.") if p["compare_at"] else \
                  f"At {price} this sits at its tracked reference price; we flag it here because we watch it hourly and will surface it the moment it drops."
        schema = json.dumps({
            "@context": "https://schema.org", "@type": "Product", "name": p["title"],
            "offers": {"@type": "Offer", "price": f"{p['price']:.2f}", "priceCurrency": p["currency"],
                       "availability": "https://schema.org/InStock" if p["available"] else "https://schema.org/OutOfStock",
                       "url": p["retailer_url"]}})
        page = PAGE.format(title=html.escape(p["title"]), price=price, was=was, retailer=p["retailer"],
                           lowest=lowest, last=p["last_checked"], slug=slug, retailer_url=p["retailer_url"],
                           amazon=amazon, ebay=ebay, verdict=verdict, schema=schema)
        open(os.path.join(args.out, slug + ".html"), "w").write(page)
        n += 1
    items = "\n".join(
        f'<li><a href="{p.get("slug") or slugify(p["title"])[:80]}.html">{html.escape(p["title"])}</a> — '
        f'{money(p["price"], p["currency"])} at {p["retailer"]}</li>' for p in products)
    index = ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
             "<title>All Tracked Espresso Deals — Price History Index | Espresso Deal Alerts</title>"
             "<meta name=\"description\" content=\"Every espresso machine and grinder we track hourly, "
             "with live prices and price history.\"></head><body>"
             "<main style=\"max-width:760px;margin:0 auto;padding:2rem 1.2rem;font-family:Georgia,serif;line-height:1.7\">"
             "<p><a href=\"../index.html\">← Espresso Deal Alerts home</a></p>"
             f"<h1>All tracked deals ({n})</h1><ul>{items}</ul></main></body></html>")
    open(os.path.join(args.out, "index.html"), "w").write(index)
    print(f"built {n} product pages + index in {args.out}/")

if __name__ == "__main__":
    main()
