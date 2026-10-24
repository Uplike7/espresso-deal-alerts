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
import argparse, json, sys, time, urllib.request, datetime

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EspressoDealAlerts/2.0"}

def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def get_product(url):
    """Return dict(title, price, compare_at, available, currency_hint) or raise."""
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
