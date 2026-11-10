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
    - the "biggest price drops right now" section: FULLY GENERATED from
      drops.json (the 7-day drop feed make_products.py rebuilds every
      run) on every run — this section went stale twice in two days
      (Nov 9/10) when hand-regenerated cards outlived the drops they
      recorded. A drop qualifies iff its product is tracked, currently
      available, still priced exactly at the dropped-to price (a price
      that has moved since the drop — in either direction — is a
      different story than the recorded step, so the card is withheld
      rather than falsified), and the step is a genuine headline drop:
      at least $25 and at least 5% (rounded), the same editorial bar
      the Nov 10 manual fix applied (Carezza $85.10/27% and La
      Specialista Arte $30.01/5% in; Philips -$0.01, Ninja -$0.95 and
      the sold-out Eletta Explore out). Cards are capped at 4, biggest
      step first, in the page's established card format; badges use the
      step basis (old -> new from drops.json), matching the section's
      own copy. With no qualifying drops the section renders an honest
      empty state — never a stale or invented card.

Prices only ever move from products.json — nothing is invented, and no
link, image, badge text, or copy is touched outside the generated
drops section. Affiliate hrefs on synced cards are never modified; the
generated drops cards are built with the same verified values the
workflow passes to build_pages.py (Amazon tag=espressodeala-20, eBay
campid=5339219076 — see AMAZON_TAG / EBAY_CAMPID below; keep them in
step with track-prices.yml).

Deliberately NOT synced (each prints a WARNING every run until a human
resolves it; see the run log):
  - Hand-placed drop-badge cards OUTSIDE the generated Black Friday
    drops section: editorial records of a specific past drop. Their
    displayed prices are left alone rather than silently falsifying
    the badge; the section needs an editorial decision, not a price
    patch. (The drops section itself is generated — see above.)
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

# Verified affiliate values for the GENERATED Black Friday drops cards.
# These must match the --amazon-tag / --ebay-campid the workflow passes
# to build_pages.py in track-prices.yml; they are the owner's confirmed
# tags (Amazon espressodeala-20, eBay campid 5339219076, both verified
# 2026-10-25). The sync never alters tags on existing cards — these are
# only used when building a new drops card from tracker data.
AMAZON_TAG = "espressodeala-20"
EBAY_CAMPID = "5339219076"

# Editorial bar for the generated "biggest price drops" section (see the
# module docstring): a recorded drop step must be at least this large in
# absolute and percentage terms (percentage rounded, as the badge shows
# it) to headline the section, and at most this many cards render.
BF_DROP_MIN_AMOUNT = 25.0
BF_DROP_MIN_PCT = 5
BF_DROP_MAX_CARDS = 4
BF_DROPS_MARKER = ('<!-- bf-drops:auto — this section is generated by '
                   'tracker/sync_editorial_prices.py from drops.json on '
                   'every tracker run; do not hand-edit -->')
BF_DROPS_EMPTY = ('<p class="drop-none">No standout price drops in the '
                  'last 7 days right now — the tracker re-checks prices '
                  'around the clock, and genuine drops land here the '
                  'moment they happen.</p>')


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
    def __init__(self, products, drops):
        self.products = products
        self.by_slug = {p["slug"]: p for p in products}
        self.by_url = {}
        for p in products:
            self.by_url.setdefault(norm_url(p["retailer_url"]), []).append(p)
        self.by_title_retailer = {}
        for p in products:
            self.by_title_retailer.setdefault((p["title"], p["retailer"]), []).append(p)
        self.drops = drops
        self.drops_slugs = {d["slug"] for d in drops if d.get("slug")}
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

    def sync_gcards(self, text, fname, skip_spans=()):
        """Sync every gcard price on the page. Articles whose start lies
        inside one of skip_spans (char offsets into text) are copied
        verbatim — used for the generated Black Friday drops section,
        whose cards sync_bf_drops has already built from tracker data
        this run, so re-warning on them would be pure noise."""
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

        pat = re.compile(r'<article class="gcard">.*?</article>', re.S)
        if not skip_spans:
            return pat.sub(repl, text)
        out, last = [], 0
        for m in pat.finditer(text):
            if any(s <= m.start() < e for s, e in skip_spans):
                continue  # inside a skip span: stays verbatim via the slices
            out.append(text[last:m.start()])
            out.append(repl(m))
            last = m.end()
        out.append(text[last:])
        return "".join(out)

    # -------- black-friday.html "biggest price drops" (generated) --------

    def bf_drops_region(self, text):
        """(start, end) char offsets of the drops section's grid INNER
        content — between the '<div class="grid">' that follows the
        'biggest price drops right now' heading and its matching close
        tag — or None when the section's structure is not found. The
        grid holds nested divs (each card has price/acts divs), so the
        close is found by depth counting, not by a first-match regex."""
        h = text.find("biggest price drops right now")
        if h < 0:
            return None
        g = text.find('<div class="grid">', h)
        if g < 0:
            return None
        start = g + len('<div class="grid">')
        depth, i = 1, start
        tag = re.compile(r'<div\b|</div>')
        while True:
            m = tag.search(text, i)
            if m is None:
                return None
            if m.group(0) == '<div':
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    return (start, m.start())
            i = m.end()

    def qualifying_drops(self):
        """[(drop, product)] from drops.json that may headline the
        section, biggest step first — see the module docstring for the
        rules. Skips that hide a significant drop (sold out, price
        moved since the drop, untracked product) warn every run;
        below-the-bar steps are routine and stay silent."""
        out = []
        for d in self.drops:
            slug = d.get("slug")
            p = self.by_slug.get(slug) if slug else None
            if p is None:
                self.warn(f"black-friday.html drops: drop for untracked product "
                          f"{slug or d.get('title')}; no card generated")
                continue
            old, new = d.get("old_price"), d.get("new_price")
            if old is None or new is None or not old > new:
                self.warn(f"black-friday.html drops: malformed drop for {p['title']} "
                          f"(old={old}, new={new}); no card generated")
                continue
            if d.get("currency") != p["currency"]:
                self.warn(f"black-friday.html drops: currency {d.get('currency')} disagrees "
                          f"with tracker {p['currency']} for {p['title']}; no card generated")
                continue
            if not p["available"]:
                self.warn(f"black-friday.html drops: {p['title']} dropped "
                          f"{money_trim(old - new)} but is sold out; no card generated")
                continue
            if abs(p["price"] - new) > 0.005:
                self.warn(f"black-friday.html drops: {p['title']} dropped to "
                          f"{money_trim(new)} on {d.get('dropped_on')}, but the tracker "
                          f"price is now {money_trim(p['price'])} — the recorded step no "
                          f"longer describes the current price; no card generated")
                continue
            amount = old - new
            if amount < BF_DROP_MIN_AMOUNT or round(amount / old * 100) < BF_DROP_MIN_PCT:
                continue  # genuine but trivial — not a headline drop
            out.append((amount, d, p))
        out.sort(key=lambda t: t[0], reverse=True)
        return [(d, p) for _, d, p in out[:BF_DROP_MAX_CARDS]]

    def bf_drop_card(self, d, p):
        """One drops-section card in the page's established format, built
        entirely from tracker data: identity, price, image and retailer
        URL from products.json; badge from the drops.json step. The
        Amazon/eBay search links carry the verified tags (module
        constants), exactly as the hand-built cards did."""
        amount = d["old_price"] - d["new_price"]
        badge = f"↓ {money_trim(amount)} ({round(amount / d['old_price'] * 100)}%)"
        title = html.escape(p["title"])
        title_attr = html.escape(p["title"], quote=True)
        img = p.get("image_url") or ""
        if img.startswith("//"):
            img = "https:" + img
        img_line = (f'\n        <img src="{html.escape(img, quote=True)}" '
                    f'alt="{title_attr}" loading="lazy">') if img else ""
        amazon = (html.escape(p["amazon_search"], quote=True)
                  + f"&tag={AMAZON_TAG}")
        ebay = (html.escape(p["ebay_search"], quote=True)
                + f"&campid={EBAY_CAMPID}&mkcid=1&mkrid=711-53200-19255-0"
                  f"&toolid=10001&customid=bf-{p['slug']}")
        return (
            f'<article class="gcard">\n'
            f'        <div><span class="pick-badge">Price Drop</span>'
            f'<span class="drop-badge">{badge}</span></div>{img_line}\n'
            f'        <h3>{title}</h3>\n'
            f'        <div class="price">{money_trim(p["price"])}'
            f'<span class="cur">{p["currency"]}</span> '
            f'<span class="ret">{html.escape(p["retailer"])}</span></div>\n'
            f'        <div class="acts">'
            f'<a class="cta" href="{html.escape(p["retailer_url"], quote=True)}" '
            f'rel="nofollow sponsored" target="_blank">Check Today’s Price &rarr;</a>'
            f'<a class="cta2" href="{amazon}" rel="nofollow sponsored" '
            f'target="_blank">Also on Amazon &rarr;</a>'
            f'<a class="cta2" href="{ebay}" rel="nofollow sponsored" '
            f'target="_blank">Also on eBay &rarr;</a></div>\n'
            f'      </article>')

    def sync_bf_drops(self, text):
        """Regenerate the drops section's grid from drops.json. Raises
        ValueError when the section's load-bearing structure is missing
        (the run then fails and nothing publishes — the section is never
        silently wiped). Returns (new_text, region) where region is the
        (start, end) of the generated content in new_text, for the
        gcard sync's skip spans."""
        region = self.bf_drops_region(text)
        if region is None:
            raise ValueError("black-friday.html: 'biggest price drops' section "
                             "structure not found; drops section NOT generated")
        cards = [self.bf_drop_card(d, p) for d, p in self.qualifying_drops()]
        body = "\n      " + BF_DROPS_MARKER + "\n      "
        body += "\n      ".join(cards) if cards else BF_DROPS_EMPTY
        new = text[:region[0]] + body + text[region[1]:]
        self.changes.append(f"black-friday.html drops section: regenerated from "
                            f"drops.json ({len(cards)} card(s))")
        return new, (region[0], region[0] + len(body))

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
                    help="drops.json feeding the generated Black Friday drops "
                         "section (default: <root>/drops.json)")
    ap.add_argument("--check", action="store_true",
                    help="report only; exit 1 if any page would change")
    args = ap.parse_args()

    try:
        products = json.load(open(args.products))
    except Exception as e:
        print(f"sync_editorial_prices: cannot read {args.products}: {e}")
        return 2
    drops_path = args.drops if args.drops is not None else f"{args.root}/drops.json"
    try:
        drops = json.load(open(drops_path)).get("drops", [])
        if not isinstance(drops, list):
            raise ValueError("drops is not a list")
    except Exception as e:
        # drops.json is now load-bearing (the Black Friday drops section
        # is generated from it), so an unreadable feed fails the run
        # instead of silently wiping the section to its empty state.
        print(f"sync_editorial_prices: cannot read {drops_path}: {e}")
        return 2

    sync = Sync(products, drops)
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
        bf, drops_region = sync.sync_bf_drops(pages["black-friday.html"])
        bf = sync.sync_gcards(bf, "black-friday.html", skip_spans=(drops_region,))
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
