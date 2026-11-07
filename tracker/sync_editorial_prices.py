#!/usr/bin/env python3
"""Sync the hand-maintained editorial pages from tracker data (structural
fix for the recurring homepage price drift, 2026-11-06).

index.html, buyers-guide.html and black-friday.html are editorial pages:
their layout and copy are hand-owned, so this script does NOT regenerate
them. It surgically rewrites ONLY the price / availability values on them
from tracker/products.json, in place, every tracker run:

  index.html
    - the ItemList JSON-LD line: offers.price + offers.availability
      for every item, matched to products by deal-page slug;
    - every product card, matched by its deals/<slug>.html link: price,
      compare-at ("was"), SAVE badge + save line (shown iff the tracker
      has compare_at > price, the same rule the original homepage
      generator in build/redesign/tools/gen_index.py uses), the sold-out
      card class / SOLD OUT badge / sold-out note (shown iff the tracker
      says unavailable — sold-out products stay listed, per owner rule);
    - the two header stats (products tracked, sold-out items tracked).
  buyers-guide.html + black-friday.html
    - every <article class="gcard"> price div, matched to a product by
      its primary CTA href (retailer URL; query string and a leading
      "www." on the host are ignored, so Amazon ?tag= links and the
      www.clivecoffee.com links match). Title and retailer on the card
      must also agree with the matched product, or the card is skipped
      with a warning instead of being "corrected" into a wrong match.
  black-friday.html only
    - the full price-list table (header "Current Price" / "Lowest
      tracked"): both price columns for every row, matched by the
      row's product name + retailer.

Prices only ever move from products.json — nothing is invented, and no
link, image, badge text, or copy is touched. Affiliate hrefs (Amazon
tag=espressodeala-20, eBay campid=5339219076) are never modified.

Deliberately NOT synced (each prints a WARNING every run until a human
resolves it; see the run log):
  - Black Friday "biggest price drops" cards carrying a drop-badge:
    editorial records of a specific past drop. Their displayed prices
    are left alone rather than silently falsifying the badge; the
    section needs an editorial decision, not a price patch.
  - Cards/rows whose product is not in the tracker (e.g. a delisted
    variant) and tracked products that have no card yet: a price sync
    cannot invent or delete editorial content.
  - Historical prices (the Black Friday page's "what did these sell
    for last Black Friday" records), price-band labels ($500 / $1,000
    filters and guide tier headings) and generic advice amounts in
    FAQs — none of those are current product prices.

Failure policy follows track_prices.py: per-item problems are reported,
never fatal; the run fails (exit 2) only when a whole input is
unreadable or a page's load-bearing structure is missing, so one bad
card can never silently take the page's other 125 prices down with it.

Usage (from tracker/, as the workflow runs it):
    python3 sync_editorial_prices.py [--products products.json]
                                     [--root ..] [--check]
--check reports what would change and exits 1 if anything would,
without writing.
"""
import argparse, html, json, re, sys, urllib.parse

SOLD_NOTE = ('<p class="sold-note">Sold out — we keep tracking it and will '
             'flag it the moment it comes back in stock.</p>')


def money_full(v, cur):
    """Homepage format, same as make_products.money: '$1,899.99' / 'CAD 1,899.99'."""
    return f"CAD {v:,.2f}" if cur == "CAD" else f"${v:,.2f}"


def money_trim(v):
    """Guide / Black Friday format: '$1,050' when whole, else '$527.20'.

    The currency is carried by the adjacent <span class="cur"> (cards) or
    the Currency column (table), so no currency prefix is emitted here.
    """
    s = f"${v:,.2f}"
    return s[:-3] if s.endswith(".00") else s


def is_discount(p):
    """The site's one discount rule (gen_index.card_html, build_pages):
    a saving exists iff compare_at is present and above the price."""
    return p.get("compare_at") is not None and p["compare_at"] > p["price"]


def pct(p):
    return round((p["compare_at"] - p["price"]) / p["compare_at"] * 100)


class Sync:
    def __init__(self, products, drops_slugs):
        self.products = products
        self.by_slug = {p["slug"]: p for p in products}
        self.by_url = {}
        for p in products:
            self.by_url.setdefault(norm_url(p["retailer_url"]), []).append(p)
        self.by_title_retailer = {}
        for p in products:
            self.by_title_retailer.setdefault((p["title"], p["retailer"]), []).append(p)
        self.drops_slugs = drops_slugs
        self.warnings = []
        self.changes = []

    def warn(self, msg):
        self.warnings.append(msg)
        print(f"::warning::sync_editorial_prices: {msg}")

    # ---------------- index.html ----------------

    def sync_index(self, text):
        text = self._sync_index_schema(text)
        text = self._sync_index_stats(text)
        text, seen = self._sync_index_cards(text)
        for slug in sorted(set(self.by_slug) - seen):
            self.warn(f"index.html: tracked product has no homepage card "
                      f"(add one editorially): {self.by_slug[slug]['title']}")
        return text

    def _sync_index_schema(self, text):
        lines = text.split("\n")
        hits = [i for i, l in enumerate(lines) if '"ItemList"' in l]
        if len(hits) != 1:
            raise ValueError(f"index.html: expected exactly 1 ItemList line, found {len(hits)}")
        i = hits[0]
        m = re.match(r'^(<script type="application/ld\+json">)(\{.*\})(</script>)$', lines[i])
        if not m:
            raise ValueError("index.html: ItemList line not in the expected script-tag shape")
        data = json.loads(m.group(2))
        n = 0
        for el in data["itemListElement"]:
            item = el["item"]
            slug = item["url"].split("/deals/")[-1].removesuffix(".html")
            p = self.by_slug.get(slug)
            if p is None:
                self.warn(f"index.html schema: no tracked product for slug {slug}; item left alone")
                continue
            offers = item["offers"]
            if (offers["priceCurrency"] != p["currency"] or offers["url"] != p["retailer_url"]
                    or item["name"] != p["title"]):
                self.warn(f"index.html schema: identity mismatch for {slug} "
                          f"(name/currency/retailer URL disagree with tracker); item left alone")
                continue
            new_price = f"{p['price']:.2f}"
            new_avail = ("https://schema.org/InStock" if p["available"]
                         else "https://schema.org/OutOfStock")
            if offers["price"] != new_price or offers["availability"] != new_avail:
                offers["price"], offers["availability"] = new_price, new_avail
                n += 1
        if n:
            lines[i] = m.group(1) + json.dumps(data) + m.group(3)
            self.changes.append(f"index.html schema: {n} item price/availability values synced")
        return "\n".join(lines)

    def _sync_index_stats(self, text):
        total = len(self.products)
        sold = sum(1 for p in self.products if not p["available"])
        for value, label in ((total, "products tracked live"),
                             (sold, "sold-out items still tracked")):
            pat = re.compile(r'(<div class="stat"><b>)(\d+)(</b><span>' + re.escape(label) + r'</span></div>)')
            text, n = pat.subn(lambda m: m.group(1) + str(value) + m.group(3), text)
            if n != 1:
                self.warn(f"index.html: stat '{label}' found {n} times (expected 1); left as-is")
            # change bookkeeping is implicit in the final file diff
        return text

    def _sync_index_cards(self, text):
        seen = set()

        def repl(m):
            card = m.group(0)
            slug_m = re.search(r'href="deals/([^"]+)\.html"', card)
            if not slug_m:
                self.warn("index.html: a card has no deals/<slug> link; left alone")
                return card
            slug = slug_m.group(1)
            p = self.by_slug.get(slug)
            if p is None:
                self.warn(f"index.html: card for untracked product {slug}; left alone")
                return card
            seen.add(slug)
            try:
                return self._sync_card(card, p)
            except ValueError as e:
                self.warn(f"index.html: card {slug} not in the expected shape ({e}); left alone")
                return card

        new = re.sub(r'<article class="card[^"]*">.*?</article>', repl, text, flags=re.S)
        return new, seen

    def _sync_card(self, card, p):
        disc = is_discount(p)
        # Article class: sold-out state only; class list is exactly
        # 'card' or 'card soldout' on this page.
        card = re.sub(r'^<article class="card(?: soldout)?">',
                      '<article class="card soldout">' if not p["available"]
                      else '<article class="card">', card, count=1)
        # Imgwrap badges: keep any condition badges (REFURBISHED / OPEN
        # BOX — they derive from the product title, which the tracker
        # owns and which does not change here) verbatim and in order;
        # rebuild the save / sold badges around them.
        m = re.search(r'(<div class="imgwrap">)(.*?)(</div>)', card, re.S)
        if not m:
            raise ValueError("no imgwrap")
        inner = m.group(2)
        head_m = re.match(r'(.*?)(?=<span class="badge|$)', inner, re.S)
        head = head_m.group(1) if head_m else inner
        cond = re.findall(r'<span class="badge cond">[^<]*</span>', inner)
        badges = ""
        if disc:
            badges += f'<span class="badge save-badge">SAVE {pct(p)}%</span>'
        badges += "".join(cond)
        if not p["available"]:
            badges += '<span class="badge sold-badge">SOLD OUT</span>'
        card = card[:m.start()] + m.group(1) + head + badges + m.group(3) + card[m.end():]
        # Price row.
        was = (f'<span class="was">{money_full(p["compare_at"], p["currency"])}</span>'
               if disc else "")
        cur = '<span class="cur">CAD</span>' if p["currency"] == "CAD" else ""
        pricerow = (f'<div class="pricerow">{was}'
                    f'<span class="price">{money_full(p["price"], p["currency"])}</span>{cur}</div>')
        card, n = re.subn(r'<div class="pricerow">.*?</div>', pricerow, card, count=1, flags=re.S)
        if n != 1:
            raise ValueError("no pricerow")
        # The two slot lines after the price row: save line, then
        # sold-out note; an unused slot is a 4-space placeholder line
        # (the homepage template's own convention).
        lines = card.split("\n")
        pi = [i for i, l in enumerate(lines) if 'class="pricerow"' in l]
        if len(pi) != 1:
            raise ValueError("pricerow line not unique")
        i = pi[0]
        save_ok = lines[i + 1] == "    " or re.fullmatch(r'    <p class="save">[^<]*</p>', lines[i + 1])
        sold_ok = lines[i + 2] == "    " or re.fullmatch(r'    <p class="sold-note">[^<]*</p>', lines[i + 2])
        if not (save_ok and sold_ok):
            raise ValueError("save/sold slot lines not in the expected shape")
        lines[i + 1] = (f'    <p class="save">Save {money_full(p["compare_at"] - p["price"], p["currency"])}'
                        f' · {pct(p)}% off compare-at</p>') if disc else "    "
        lines[i + 2] = f"    {SOLD_NOTE}" if not p["available"] else "    "
        return "\n".join(lines)

    # ---------------- buyers-guide.html / black-friday.html ----------------

    def match_gcard(self, card):
        """(product, how) or (None, reason). Primary key: the card's CTA
        href is the product's retailer URL — Amazon cards carry their
        ?tag= query, Clive cards a www. host; both are normalized away.
        Fallback: exact (title, retailer)."""
        cta = re.search(r'<a class="cta" href="([^"]+)"', card)
        h3 = re.search(r'<h3>([^<]+)</h3>', card)
        title = html.unescape(h3.group(1)) if h3 else ""
        if cta:
            cands = self.by_url.get(norm_url(cta.group(1)), [])
            if len(cands) == 1:
                return cands[0], "cta-url"
            if len(cands) > 1:
                return None, f"CTA URL matches {len(cands)} tracked products"
        ret_m = re.search(r'<span class="ret">([^<]+)</span>', card)
        retailer = html.unescape(ret_m.group(1)) if ret_m else ""
        cands = self.by_title_retailer.get((title, retailer), [])
        if len(cands) == 1:
            return cands[0], "title+retailer"
        return None, "no unique tracked product for this card"

    def sync_gcards(self, text, fname):
        def repl(m):
            card = m.group(0)
            p, how = self.match_gcard(card)
            h3 = re.search(r'<h3>([^<]+)</h3>', card)
            title = html.unescape(h3.group(1)) if h3 else "(no title)"
            if 'drop-badge' in card:
                cur = re.search(r'<div class="price">([^<]+)<', card)
                extra = ""
                if p is not None:
                    extra = (f"; tracker price is now {money_trim(p['price'])} "
                             f"{p['currency']} (card shows {cur.group(1) if cur else '?'})"
                             + ("" if p["slug"] in self.drops_slugs
                                else "; the product has no drop in the current drops.json window"))
                self.warn(f"{fname}: drop-badge card left editorial (badge records a specific "
                          f"past drop; price not auto-synced): {title}{extra}")
                return card
            if p is None:
                self.warn(f"{fname}: card matches {how}; left alone: {title}")
                return card
            # Identity cross-checks: never "fix" a card into a different
            # product than the one its title/retailer describe.
            ret_m = re.search(r'<span class="ret">([^<]+)</span>', card)
            if html.unescape(ret_m.group(1)) != p["retailer"] or title != p["title"]:
                self.warn(f"{fname}: card matched by {how} but its title/retailer disagree "
                          f"with the tracker ({title} @ {html.unescape(ret_m.group(1))} vs "
                          f"{p['title']} @ {p['retailer']}); left alone")
                return card

            def price_repl(pm):
                return (f'<div class="price">{money_trim(p["price"])}'
                        f'<span class="cur">{p["currency"]}</span> '
                        f'<span class="ret">{pm.group(2)}</span></div>')

            new, n = re.subn(
                r'<div class="price">[^<]*<span class="cur">[^<]+</span> '
                r'(<span class="ret">([^<]+)</span>)</div>',
                lambda pm: price_repl(pm), card, count=1)
            if n != 1:
                self.warn(f"{fname}: price div not in the expected shape; card left alone: {title}")
                return card
            return new

        return re.sub(r'<article class="gcard">.*?</article>', repl, text, flags=re.S)

    def sync_bf_table(self, text):
        """The full price-list table on black-friday.html: 'Current Price'
        and 'Lowest tracked' are live tracker values and go stale like
        any card price (the Nov 6 bundle missed this table entirely)."""
        tables = list(re.finditer(r'<table[^>]*>.*?</table>', text, flags=re.S))
        target = [t for t in tables
                  if "Current Price" in t.group(0) and "Lowest tracked" in t.group(0)]
        if not target:
            self.warn("black-friday.html: no price-list table found; table prices NOT synced")
            return text
        for t in target:
            def row_repl(m):
                # groups: 1 name, 2 currency, 3 retailer, 4 best-for
                name, retailer = html.unescape(m.group(1)), html.unescape(m.group(3))
                cands = self.by_title_retailer.get((name, retailer), [])
                if len(cands) != 1:
                    self.warn(f"black-friday.html table: row matches {len(cands)} tracked "
                              f"products; left alone: {name}")
                    return m.group(0)
                p = cands[0]
                if m.group(2) != p["currency"]:
                    self.warn(f"black-friday.html table: currency cell {m.group(2)} disagrees "
                              f"with tracker {p['currency']} for {name}; row left alone")
                    return m.group(0)
                return (f'<tr><td>{m.group(1)}</td>'
                        f'<td><strong>{money_trim(p["price"])}</strong></td>'
                        f'<td>{money_trim(p["lowest_tracked"])}</td>'
                        f'<td>{m.group(2)}</td><td>{m.group(3)}</td><td>{m.group(4)}</td></tr>')
            new_table = re.sub(
                r'<tr><td>([^<]+)</td><td><strong>[^<]+</strong></td><td>[^<]+</td>'
                r'<td>([^<]+)</td><td>([^<]+)</td><td>([^<]+)</td></tr>',
                row_repl, t.group(0))
            text = text.replace(t.group(0), new_table, 1)
        return text


def norm_url(u):
    """Normalize a retailer URL for matching: drop the query string and
    fragment, and drop a leading 'www.' on the host. Nothing else — paths
    and schemes must agree exactly."""
    parts = urllib.parse.urlsplit(u.strip())
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", default="products.json")
    ap.add_argument("--root", default="..",
                    help="repo root holding the three pages (default: .., as in the workflow)")
    ap.add_argument("--drops", default=None,
                    help="drops.json for warning context (default: <root>/drops.json if present)")
    ap.add_argument("--check", action="store_true",
                    help="report only; exit 1 if any page would change")
    args = ap.parse_args()

    try:
        products = json.load(open(args.products))
    except Exception as e:
        print(f"sync_editorial_prices: cannot read {args.products}: {e}")
        return 2
    drops_path = args.drops if args.drops is not None else f"{args.root}/drops.json"
    drops_slugs = set()
    try:
        drops_slugs = {d["slug"] for d in json.load(open(drops_path)).get("drops", [])}
    except Exception:
        pass  # warning context only; never fatal

    sync = Sync(products, drops_slugs)
    pages = {}
    try:
        for fname in ("index.html", "buyers-guide.html", "black-friday.html"):
            pages[fname] = open(f"{args.root}/{fname}").read()
    except Exception as e:
        print(f"sync_editorial_prices: cannot read a page under {args.root}: {e}")
        return 2

    out = {}
    try:
        out["index.html"] = sync.sync_index(pages["index.html"])
        out["buyers-guide.html"] = sync.sync_gcards(pages["buyers-guide.html"], "buyers-guide.html")
        bf = sync.sync_gcards(pages["black-friday.html"], "black-friday.html")
        out["black-friday.html"] = sync.sync_bf_table(bf)
    except ValueError as e:
        print(f"sync_editorial_prices: {e}")
        return 2

    changed = [f for f in pages if out[f] != pages[f]]
    for f in changed:
        print(f"sync_editorial_prices: {f} updated from tracker data")
    for note in sync.changes:
        print(f"sync_editorial_prices: {note}")
    print(f"sync_editorial_prices: {len(changed)} page(s) changed, "
          f"{len(sync.warnings)} warning(s)")
    if args.check:
        return 1 if changed else 0
    for f in changed:
        open(f"{args.root}/{f}", "w").write(out[f])
    return 0


if __name__ == "__main__":
    sys.exit(main())
