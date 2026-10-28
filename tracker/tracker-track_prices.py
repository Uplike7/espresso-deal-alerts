#!/usr/bin/env python3
"""Espresso Deal Alerts — price tracker (Muse rebuild, 2026-10-23).

Fetches live prices for every tracked retailer product via Shopify's public
product JSON endpoints (.js on most stores, .json on iDrinkCoffee), compares
against price-history.json, and appends a dated snapshot. Designed to run
server-side (GitHub Actions hourly or Muse cron) — never on a local laptop.

Usage: python3 track_prices.py [--catalog catalog-urls.txt] [--history price-history.json]
Exit code 0 always unless the catalog itself is unreadable; per-product
failures are reported, not fatal.
"""
import argparse, html as html_lib, json, re, sys, time, urllib.request, datetime

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EspressoDealAlerts/2.0"}

# Amazon product pages have no JSON endpoint; they are parsed from the HTML
# buy-box block. A realistic browser UA is required or Amazon serves a
# robot-check page instead of the listing (detected and treated as a
# per-product failure, never a fabricated price).
AMAZON_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
             "Accept-Language": "en-US,en;q=0.9",
             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def _fetch_amazon_once(url):
    """Single attempt: fetch and parse an amazon.com/dp/ product page.
    Returns the same dict shape as the Shopify path. compare_at is None:
    Amazon's list-price markup is not consistent enough across layouts to
    read honestly, and a missing compare-at renders as 'no recorded saving'
    rather than a fake discount. Raises on any failure, including a
    robot-check page — a bot wall is never read as a price."""
    req = urllib.request.Request(url, headers=AMAZON_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        h = r.read().decode("utf-8", "replace")
    if "Robot Check" in h or "Enter the characters you see below" in h:
        raise ValueError("amazon robot check")
    mt = re.search(r'id="productTitle"[^>]*>(.*?)</span>', h, re.S)
    if not mt:
        raise ValueError("no product title")
    title = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", mt.group(1))).split())
    price = None
    for anchor in (r'id="corePrice_feature_div"', r'id="priceToPay"'):
        seg = re.search(anchor + r'.{0,9000}', h, re.S)
        if seg:
            pm = re.search(r'a-offscreen">\$([\d,]+\.\d{2})<', seg.group(0))
            if pm:
                price = float(pm.group(1).replace(",", ""))
                break
    if price is None:
        raise ValueError("no buy-box price")
    av = re.search(r'id="availability".{0,600}?</span>', h, re.S)
    avtext = " ".join(re.sub(r"<[^>]+>", " ", av.group(0)).split()).lower() if av else ""
    return {"title": title, "price": price, "compare_at": None,
            "available": "unavailable" not in avtext}

def get_amazon_product(url):
    """Fetch an Amazon product with up to 3 attempts and backoff (2s, 4s).

    Amazon intermittently serves robot-check or title-less pages to
    datacenter IPs (notably GitHub Actions runners), so a single attempt
    fails products that are perfectly reachable on a retry. Every attempt
    still goes through the full parse: a robot-check page raises and burns
    the attempt — it is never treated as a price. After the last attempt
    the original error propagates and the product is a per-run failure;
    its price-history entry (if any) is left untouched by the caller."""
    last_err = None
    for attempt in range(3):
        if attempt:
            time.sleep(2 * attempt)
        try:
            return _fetch_amazon_once(url)
        except Exception as e:
            last_err = e
    raise last_err

def get_product(url):
    """Return dict(title, price, compare_at, available, currency_hint) or raise."""
    if "amazon." in url:
        return get_amazon_product(url)
    # iDrinkCoffee blocks .js (returns HTML); its .json endpoint works.
    endpoints = [url + ".json", url + ".js"] if "idrinkcoffee" in url else [url + ".js", url + ".json"]
    last_err = None
    for ep in endpoints:
        try:
            data = fetch_json(ep)
            prod = data.get("product", data)
            variants = prod.get("variants", [])
            if not variants:
                raise ValueError("no variants")
            v = variants[0]
            # Shopify .js returns money in CENTS; the .json endpoint returns dollars.
            scale = 100.0 if ep.endswith(".js") else 1.0
            price = float(v["price"]) / scale
            cap = v.get("compare_at_price")
            return {
                "title": prod.get("title", "").strip(),
                "price": price,
                "compare_at": float(cap) / scale if cap else None,
                "available": bool(v.get("available", True)),
            }
        except Exception as e:  # try next endpoint shape
            last_err = e
    raise last_err

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="catalog-urls.txt")
    ap.add_argument("--history", default="price-history.json")
    args = ap.parse_args()

    urls = [l.strip() for l in open(args.catalog) if l.strip()]
    try:
        history = json.load(open(args.history))
    except Exception:
        history = {}
    # Prune history to the current catalog so removed products disappear everywhere.
    history = {u: e for u, e in history.items() if u in set(urls)}
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ok, fail, drops = 0, [], []
    for url in urls:
        try:
            p = get_product(url)
            prev = history.get(url, {}).get("current")
            entry = history.setdefault(url, {"title": p["title"], "snapshots": []})
            entry["title"] = p["title"]
            entry["current"] = p["price"]
            entry["snapshots"].append({"t": now, "price": p["price"],
                                       "compare_at": p["compare_at"], "available": p["available"]})
            if prev is not None and p["price"] < prev:
                drops.append((url, prev, p["price"]))
            ok += 1
        except Exception as e:
            fail.append((url, str(e)[:120]))
        time.sleep(0.4)

    json.dump(history, open(args.history, "w"), indent=1)
    print(f"checked {len(urls)}: ok={ok} failed={len(fail)} price_drops={len(drops)} at {now}")
    for u, o, n in drops:
        print(f"  DROP {o} -> {n}  {u}")
    for u, e in fail:
        print(f"  FAIL {u} :: {e}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
