#!/usr/bin/env python3
"""Build one SEO page per tracked product from products.json.

Each page: unique title/meta targeting '<Model> price / deals', live price +
compare-at + tracked-low, a REAL price-history table from price-history.json,
related models, a per-product alert button, Product/Offer + BreadcrumbList
JSON-LD, a breadcrumb trail to the product's category hub, FAQ block
(AEO), and outbound links.

AFFILIATE HANDLING — READ BEFORE TOUCHING:
- Primary "View deal" retailer links: params come ONLY from
  RETAILER_AFFILIATE_PARAMS below. It is EMPTY ON PURPOSE: no retailer
  affiliate tag has been verified yet (Impact brand contracts were still
  pending as of 2026-10-25). Add an entry ONLY with a tag confirmed in that
  retailer's own affiliate dashboard. Never invent one.
- Secondary search links: Amazon and eBay params come from --amazon-tag /
  --ebay-campid (the hourly workflow passes the owner's verified Store ID
  espressodeala-20 and eBay campid 5339219076). Do NOT move those tags onto
  retailer links: both are VERIFIED as the owner's.
- Product photos: a photo renders ONLY if the product record carries an
  "image_url" field. No product in products.json carries one today, so pages
  currently render without a photo. Do NOT hotlink retailer/homepage images
  by title-matching — that would be guessing URLs. When the tracker starts
  recording image_url in products.json, photos appear automatically.

CATEGORY HEURISTIC: products.json has no category field, so "machine" /
"grinder" / "other" below is derived from title keywords (grinder checked
first — e.g. a machine "with Built-In Grinder" classifies as grinder). It
drives related-models lists and hub-page membership only, never prices.
"""
import argparse, html, json, re, urllib.parse

# ---------------------------------------------------------------------------
# Per-retailer affiliate params for PRIMARY "View deal" links.
# Format: "Retailer Display Name": {"param_name": "verified_value", ...}
# EMPTY BY DEFAULT. Fill only with verified tags. When filled, the params are
# appended to that retailer's retailer_url on every deal page automatically.
# ---------------------------------------------------------------------------
RETAILER_AFFILIATE_PARAMS = {
    # Example (DO NOT USE — placeholder shape only):
    # "Whole Latte Love": {"utm_source": "espressodealalerts"},
    # Amazon Associates Store ID, confirmed by the owner from Associates
    # Central (2026-10-25); the same verified tag the workflow passes for
    # Amazon search links via --amazon-tag. Amazon-tracked products (added
    # 2026-10-28) are sold BY Amazon, so the tag belongs on their primary
    # "View deal" links as well.
    "Amazon": {"tag": "espressodeala-20"},
}

def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")

def money(v, cur):
    return f"{'CAD ' if cur == 'CAD' else '$'}{v:,.2f}" if cur == 'CAD' else f"${v:,.2f}"

def category(title):
    """Title-keyword category heuristic (see module docstring)."""
    t = title.lower()
    if "grinder" in t:
        return "grinder"
    if "machine" in t:
        return "machine"
    return "other"

def fmt_last_checked(iso):
    """'2026-10-26T14:50:03Z' -> 'Oct 26, 2026, 10:50 AM EDT' (America/New_York).

    Returns '' when the value is missing or unparseable, so the template
    drops the segment instead of showing a fabricated time.
    """
    if not iso:
        return ""
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(ZoneInfo("America/New_York"))
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        h12 = local.hour % 12 or 12
        ampm = "AM" if local.hour < 12 else "PM"
        return (f"{months[local.month - 1]} {local.day}, {local.year}, "
                f"{h12}:{local.minute:02d} {ampm} {local.tzname()}")
    except Exception:
        return ""

def with_params(url, params):
    """Append query params to a URL, preserving any existing query string."""
    if not params:
        return url
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    q.extend(params.items())
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path,
         urllib.parse.urlencode(q), parts.fragment))

def history_segments(snapshots):
    """Collapse consecutive snapshots at the same price into price periods.

    Returns a list of dicts: first_seen, last_seen, price, compare_at,
    available. Every date/price comes from price-history.json; nothing here
    is estimated or invented.
    """
    segs = []
    for s in snapshots:
        if segs and segs[-1]["price"] == s["price"] and segs[-1]["compare_at"] == s.get("compare_at"):
            segs[-1]["last_seen"] = s["t"]
            segs[-1]["available"] = s.get("available", True)
        else:
            segs.append({"first_seen": s["t"], "last_seen": s["t"], "price": s["price"],
                         "compare_at": s.get("compare_at"), "available": s.get("available", True)})
    return segs

def history_table(p, history):
    """HTML price-history table for one product, or '' if no history exists."""
    snaps = history.get(p["retailer_url"], {}).get("snapshots", [])
    if not snaps:
        return ""
    segs = history_segments(snaps)
    # Most recent segments first, capped so the table stays readable.
    rows = ""
    for seg in reversed(segs[-12:]):
        first = seg["first_seen"][:10]
        last = seg["last_seen"][:10]
        span = first if first == last else f"{first} → {last}"
        cap = money(seg["compare_at"], p["currency"]) if seg["compare_at"] else "—"
        stock = "In stock" if seg["available"] else "Out of stock"
        rows += (f"<tr><td>{span}</td><td>{money(seg['price'], p['currency'])}</td>"
                 f"<td>{cap}</td><td>{stock}</td></tr>")
    return (f"<h2>Price history</h2>"
            f"<p>Tracked since {snaps[0]['t'][:10]} · {len(snaps)} price checks. "
            f"Each row is a period the price held at that level.</p>"
            f"<table class=\"history\">"
            f"<tr><th scope=\"col\">Date(s)</th><th scope=\"col\">Price</th><th scope=\"col\">Compare-at</th><th scope=\"col\">Stock</th></tr>"
            f"{rows}</table>")

def hub_for(p, refill_slugs):
    """(relative href, display name, absolute URL) of this product's hub.

    Refills-catalog products belong to the refills hub; the rest follow the
    title-keyword category heuristic. Membership comes from data files
    (refills-catalog.json / product title), never from a per-page guess.
    """
    if slugify(p["title"])[:80] in refill_slugs:
        return ("../refills.html", "Refills & consumables",
                "https://espressodealalerts.com/refills.html")
    c = category(p["title"])
    if c == "machine":
        return ("../machines.html", "Espresso machines",
                "https://espressodealalerts.com/machines.html")
    if c == "grinder":
        return ("../grinders.html", "Espresso grinders",
                "https://espressodealalerts.com/grinders.html")
    return ("index.html", "All tracked deals",
            "https://espressodealalerts.com/deals/")

def related_models(p, products, limit=4):
    """Up to `limit` links: same retailer first, then same title category."""
    slug = p.get("slug") or slugify(p["title"])[:80]
    same_retailer = [q for q in products
                     if q["retailer"] == p["retailer"]
                     and (q.get("slug") or slugify(q["title"])[:80]) != slug]
    same_cat = [q for q in products
                if category(q["title"]) == category(p["title"])
                and q["retailer"] != p["retailer"]
                and (q.get("slug") or slugify(q["title"])[:80]) != slug]
    picks = (same_retailer + same_cat)[:limit]
    if not picks:
        return ""
    items = "".join(
        f'<li><a href="{q.get("slug") or slugify(q["title"])[:80]}.html">{html.escape(q["title"])}</a> — '
        f'{money(q["price"], q["currency"])} at {q["retailer"]}</li>' for q in picks)
    return f"<h2>Related models</h2><ul>{items}</ul>"

# ---------------------------------------------------------------------------
# Deal-page visual system (light cream/espresso/copper). PRESENTATION ONLY:
# this CSS is the only thing the redesign changes in the page template. All
# placeholders, logic, affiliate handling, and data below are untouched.
# It is injected via the __DEAL_CSS__ token AFTER .format() runs, so the
# braces in CSS never collide with format placeholders.
# ---------------------------------------------------------------------------
GA4_SNIPPET = """<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-4L0H4N4G2T"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-4L0H4N4G2T');
</script>"""

INDEX_HEAD = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<meta property="og:title" content="All Tracked Espresso Deals — Price History Index | Espresso Deal Alerts">
<meta property="og:description" content="Every espresso machine and grinder we track around the clock, with live prices and price history.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://espressodealalerts.com/deals/">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="All Tracked Espresso Deals — Price History Index | Espresso Deal Alerts">
<meta name="twitter:description" content="Every espresso machine and grinder we track around the clock, with live prices and price history.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-4L0H4N4G2T"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-4L0H4N4G2T');
</script>"""

DEAL_CSS = """
body{background:#faf5ec;color:#2b1d12;font-family:Inter,system-ui,sans-serif;line-height:1.6;margin:0}
.wrap{max-width:760px;margin:0 auto;padding:2rem 1.2rem}
a{color:#a85e17}
h1,h2,h3{font-family:Fraunces,Georgia,serif}
.back a{text-decoration:none;font-weight:600}
.crumbs{font-size:.92rem;color:#6b5b4c;margin-bottom:.4rem}
.crumbs a{color:#6b5b4c;font-weight:600;text-decoration:none}
.crumbs a:hover{color:#a85e17}
.btn-primary{display:inline-block;background:#2b1d12;color:#faf5ec;text-decoration:none;padding:.8rem 1.6rem;border-radius:12px;font-weight:700}
.btn-primary:hover{background:#a85e17}
.btn-alert{display:inline-block;border:1px solid #a85e17;color:#2b1d12;padding:.55rem 1.1rem;border-radius:10px;text-decoration:none;font-weight:600}
.btn-alert:hover{border-color:#2b1d12}
.quiet{color:#6b5b4c;font-size:.92rem}
.quiet a{color:#6b5b4c}
table.history{border-collapse:collapse;width:100%;margin:1rem 0}
table.history th,table.history td{border:1px solid #e6dacb;padding:.5rem .7rem;text-align:left}
table.history th{background:#f5e7d3}
.disclosure{font-size:.9em;color:#6b5b4c}
img{max-width:100%;border-radius:12px}
:focus-visible{outline:2px solid #a85e17;outline-offset:2px}
"""

DEAL_EVENTS = """<script>(function () {
  // GA4 conversion events for outbound deal clicks on this page. Reporting
  // only: links keep their default behavior, and a missing or blocked gtag
  // is a silent no-op. The alert button is not a view-deal click and is
  // intentionally untracked.
  function track(params) {
    try { if (typeof gtag === "function") { gtag("event", "view_deal", params); } } catch (err) {}
  }
  function trunc(s) { s = (s || "").trim(); return s.length > 100 ? s.slice(0, 100) : s; }
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a") : null;
    if (!a) return;
    var href = a.getAttribute("href") || "";
    var titleEl = document.querySelector("h1");
    var title = titleEl ? titleEl.textContent : "";
    if (a.classList.contains("btn-primary")) {
      track({link_type: "retailer", retailer: trunc(a.getAttribute("data-ga-retailer") || ""), product: trunc(title)});
    } else if (href.indexOf("amazon.com") !== -1) {
      track({link_type: "amazon", product: trunc(title)});
    } else if (href.indexOf("ebay.com") !== -1) {
      track({link_type: "ebay", product: trunc(title)});
    }
  });
})();</script>"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} Price, Deals &amp; Price History — Espresso Deal Alerts</title>
<meta name="description" content="Live price for the {title}: {price} at {retailer}. Tracked against price history — lowest tracked {lowest}. Verified deals, no fake sales.">
<link rel="canonical" href="https://espressodealalerts.com/deals/{slug}.html">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<meta property="og:title" content="{title} — Espresso Deal Alerts">
<meta property="og:description" content="Live price for the {title}: {price} at {retailer}. Tracked against price history.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://espressodealalerts.com/deals/{slug}.html">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title} — Espresso Deal Alerts">
<meta name="twitter:description" content="Live price for the {title}: {price} at {retailer}. Tracked against price history.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
__GA4__
<script type="application/ld+json">{schema}</script>
<script type="application/ld+json">{crumb_schema}</script>
<style>__DEAL_CSS__</style>
</head><body>
<main class="wrap">
<nav class="crumbs" aria-label="Breadcrumb"><a href="../index.html">Home</a> › <a href="{hub_href}">{hub_name}</a> › <span aria-current="page">{title}</span></nav>
<h1>{title}</h1>
{photo}
<p><strong>Current price: {price}</strong>{was} at {retailer}</p>
{stock}
<p>Lowest price we've tracked: <strong>{lowest}</strong>{lastseg}</p>
<p><a class="btn-primary" href="{retailer_url}" rel="sponsored nofollow" data-ga-retailer="{retailer_attr}">View deal at {retailer} →</a></p>
<p><a class="btn-alert" href="../index.html?alert={alert_q}">🔔 Alert me when this drops</a></p>
<p class="quiet">Also check: <a href="{amazon}" rel="sponsored nofollow">Amazon</a> · <a href="{ebay}" rel="sponsored nofollow">eBay</a></p>
<h2>Is this a good price?</h2>
<p>{verdict}</p>
{history}
{related}
{refills_link}
<h2>Questions</h2>
<h3>What is the lowest price the {title} has been?</h3>
<p>Since we began tracking it, the lowest recorded price is {lowest} (current retailer listing: {price} at {retailer}).</p>
<h3>How often does the price change?</h3>
<p>We check this product's price several times a day across specialty retailers and log every change to our price history.</p>
<p class="disclosure">Affiliate disclosure: links on this page may earn us a commission at no extra cost to you. Prices update several times a day; retailer prices prevail at checkout.</p>
</main>__DEAL_EVENTS__</body></html>"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", default="products.json")
    ap.add_argument("--history", default="price-history.json")
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
    try:
        history = json.load(open(args.history))
    except Exception:
        history = {}
    # Slugs of the refills/consumables catalog (same directory as products),
    # used only to route breadcrumbs/cross-links to the refills hub.
    import os.path
    refill_slugs = set()
    try:
        rc = json.load(open(os.path.join(
            os.path.dirname(os.path.abspath(args.products)), "refills-catalog.json")))
        refill_slugs = {e["slug"] for e in rc if e.get("slug")}
    except Exception:
        refill_slugs = set()
    n = 0
    for p in products:
        slug = p.get("slug") or slugify(p["title"])[:80]
        amazon = p["amazon_search"] + (f"&tag={args.amazon_tag}" if args.amazon_tag else "")
        ebay = p["ebay_search"] + (f"&campid={args.ebay_campid}" if args.ebay_campid else "")
        # Primary retailer link: affiliate params ONLY from the verified config.
        retailer_url = with_params(p["retailer_url"],
                                   RETAILER_AFFILIATE_PARAMS.get(p["retailer"], {}))
        price = money(p["price"], p["currency"]); lowest = money(p["lowest_tracked"], p["currency"])
        was = f" (was {money(p['compare_at'], p['currency'])})" if p["compare_at"] else ""
        # Visible sold-out marking: sold-out products stay listed and tracked,
        # but the page must say so plainly (previously only the JSON-LD said it).
        stock = "" if p["available"] else (
            f'<p><strong style="color:#a33">Sold out at {p["retailer"]}</strong> '
            f'— we keep tracking it and will flag it the moment it comes back in stock.</p>')
        saving = (p["compare_at"] - p["price"]) if p["compare_at"] else 0
        verdict = (f"At {price} this is {money(saving, p['currency'])} below its compare-at price"
                   f"{' and at the lowest price we have tracked' if abs(p['price']-p['lowest_tracked'])<0.01 else ''}. "
                   "Our verdicts follow the tracked data, not retailer marketing.") if p["compare_at"] else \
                  f"At {price} this sits at its tracked reference price; we flag it here because we watch it around the clock and will surface it the moment it drops."
        schema = json.dumps({
            "@context": "https://schema.org", "@type": "Product", "name": p["title"],
            "offers": {"@type": "Offer", "price": f"{p['price']:.2f}", "priceCurrency": p["currency"],
                       "availability": "https://schema.org/InStock" if p["available"] else "https://schema.org/OutOfStock",
                       "url": retailer_url}})
        hub_href, hub_name, hub_abs = hub_for(p, refill_slugs)
        crumb_schema = json.dumps({
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home",
                 "item": "https://espressodealalerts.com/"},
                {"@type": "ListItem", "position": 2, "name": hub_name, "item": hub_abs},
                {"@type": "ListItem", "position": 3, "name": p["title"],
                 "item": f"https://espressodealalerts.com/deals/{slug}.html"}]})
        # Cross-link to the refills shelf from machine/grinder pages only —
        # beans/filters/descaler are the consumables those owners rebuy.
        refills_link = (
            '<p class="quiet">Keep it running: beans, filters, descaler and cleaning '
            'supplies we track live on the <a href="../refills.html">Refills &amp; '
            'consumables</a> page.</p>'
            if category(p["title"]) in ("machine", "grinder")
            and slugify(p["title"])[:80] not in refill_slugs else "")
        # Photo ONLY from an image_url recorded in products.json (none today).
        photo = ""
        if p.get("image_url"):
            photo = (f'<p><img src="{html.escape(p["image_url"])}" alt="{html.escape(p["title"])}" '
                     f'loading="lazy" style="max-width:100%"></p>')
        last_txt = fmt_last_checked(p.get("last_checked"))
        lastseg = f" · Last checked {last_txt}" if last_txt else ""
        page = PAGE.format(title=html.escape(p["title"]), price=price, was=was, retailer=p["retailer"],
                           lowest=lowest, lastseg=lastseg, retailer_attr=html.escape(p["retailer"], quote=True), slug=slug, retailer_url=retailer_url,
                           amazon=amazon, ebay=ebay, verdict=verdict, schema=schema, photo=photo,
                           crumb_schema=crumb_schema, hub_href=hub_href,
                           hub_name=html.escape(hub_name), refills_link=refills_link,
                           history=history_table(p, history), related=related_models(p, products),
                           alert_q=urllib.parse.quote_plus(p["title"]), stock=stock).replace(
            "__DEAL_CSS__", DEAL_CSS).replace("__GA4__", GA4_SNIPPET).replace("__DEAL_EVENTS__", DEAL_EVENTS)
        open(os.path.join(args.out, slug + ".html"), "w").write(page)
        n += 1
    items = "\n".join(
        f'<li><a href="{p.get("slug") or slugify(p["title"])[:80]}.html">{html.escape(p["title"])}</a> — '
        f'{money(p["price"], p["currency"])} at {p["retailer"]}'
        f'{" — <strong>Sold out</strong>" if not p["available"] else ""}</li>' for p in products)
    itemlist = json.dumps({
        "@context": "https://schema.org", "@type": "ItemList",
        "name": "All tracked espresso deals",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1,
             "url": f"https://espressodealalerts.com/deals/{p.get('slug') or slugify(p['title'])[:80]}.html",
             "name": p["title"]} for i, p in enumerate(products)]})
    index = ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
             "<title>All Tracked Espresso Deals — Price History Index | Espresso Deal Alerts</title>"
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" "<meta name=\"description\" content=\"Every espresso machine and grinder we track around the clock, "
             "with live prices and price history.\">__INDEX_HEAD__"
             f"<script type=\"application/ld+json\">{itemlist}</script>"
             "<style>__DEAL_CSS__</style></head><body>"
             "<main class=\"wrap\">"
             "<p class=\"back\"><a href=\"../index.html\">← Espresso Deal Alerts home</a></p>"
             f"<h1>All tracked deals ({n})</h1><ul>{items}</ul></main></body></html>").replace(
        "__DEAL_CSS__", DEAL_CSS).replace("__INDEX_HEAD__", INDEX_HEAD)
    open(os.path.join(args.out, "index.html"), "w").write(index)
    print(f"built {n} product pages + index in {args.out}/")

if __name__ == "__main__":
    main()
