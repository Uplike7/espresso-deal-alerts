#!/usr/bin/env python3
"""Alert subscribers about NEW price drops detected by the tracker.

Runs as one step of the hourly track-prices workflow, AFTER
make_products.py has regenerated drops.json at the repo root.

What it does
------------
1. Loads drops.json (the tracker's "most recent genuine drop per product
   within 7 days" feed, written by make_products.build_drops).
2. Loads alert-state.json — the set of drop keys already alerted. A drop's
   key is slug + old price + new price + drop date, so a drop that lingers
   in the 7-day drops.json window never alerts twice, while a FURTHER drop
   on the same product (new prices / new date) is a new key and alerts
   again — which is what subscribers want.
3. For drops never alerted, formats ONE digest (plain text + HTML) and:
   - sends it to the Telegram channel via the Bot API, IF both
     TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set as environment
     variables (GitHub secrets in the workflow). Uses urllib only —
     no new dependencies.
   - ALWAYS also writes the digest to alerts-outbox/<date>-<hash>.md —
     the email-ready copy. There is deliberately NO email sender here:
     email needs a real provider account + API key (see BUILD-REPORT.md).
4. Marks keys as sent in alert-state.json ONLY after a Telegram send
   actually succeeds. If credentials are missing, it logs
   "alerts pending: no Telegram credentials configured", leaves state
   untouched, and exits 0 — so the moment credentials are added, the
   pending drops go out on the next hourly run.

First-run behaviour (baseline seeding)
--------------------------------------
If alert-state.json does not exist at all, this is treated as the first
ever run: every drop currently in drops.json is recorded as already-seen
(the "baseline") and NO alert is sent. Rationale: drops.json holds up to
7 days of history; blasting the whole backlog to a brand-new channel on
day one would spam subscribers with stale deals. Only drops that appear
AFTER the baseline get alerted. Delete alert-state.json to re-seed.

Failure behaviour
-----------------
This script must never break the hourly price refresh: missing or
malformed drops.json, malformed entries, Telegram network errors — all
are logged and exit 0. (The workflow step additionally carries
continue-on-error as a second layer.)

Paths (all overridable by environment, used by the tests)
--------------------------------------------------------
ALERT_DROPS_PATH   default: ./drops.json, else ../drops.json
                   (the workflow runs this from tracker/, drops.json
                   lives at the repo root)
ALERT_STATE_PATH   default: ./alert-state.json  (tracker/alert-state.json
                   in the workflow; committed by the workflow so the
                   "already sent" memory survives between runs)
ALERT_OUTBOX_DIR   default: ./alerts-outbox
TELEGRAM_API_BASE  default: https://api.telegram.org (test hook only)

Python 3.12, standard library only.
"""
import hashlib
import html
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://uplike7.github.io/espresso-deal-alerts/"
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
TELEGRAM_TEXT_LIMIT = 4096  # Bot API hard limit for one message


def log(msg):
    print(f"[alert_drops] {msg}", flush=True)


def money(v, cur):
    """Same formatting convention as make_products.py."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"CAD {v:,.2f}" if cur == "CAD" else f"${v:,.2f}"


def drop_key(d):
    """Stable identity of one drop event: slug + prices + drop date."""
    return "{slug}|{old}|{new}|{date}".format(
        slug=d.get("slug", ""), old=d.get("old_price"),
        new=d.get("new_price"), date=d.get("dropped_on", ""))


def valid_drop(d):
    return isinstance(d, dict) and d.get("slug") and \
        isinstance(d.get("old_price"), (int, float)) and \
        isinstance(d.get("new_price"), (int, float)) and \
        d["new_price"] < d["old_price"]


def pct_off(d):
    return (d["old_price"] - d["new_price"]) / d["old_price"] * 100.0


def format_digest(drops, when=None):
    """Return (plain_text, html) digests for a list of new drops."""
    when = when or datetime.now(timezone.utc)
    date_label = when.strftime("%b %-d, %Y") if hasattr(when, "strftime") else str(when)
    n = len(drops)
    heading = f"New espresso price drop{'s' if n != 1 else ''} ({date_label})"

    lines = [f"☕ {heading}", ""]
    items = []
    for d in drops:
        deal_url = f"{BASE}deals/{d['slug']}.html"
        prices = f"{money(d['old_price'], d.get('currency'))} → " \
                 f"{money(d['new_price'], d.get('currency'))} (−{pct_off(d):.1f}%)"
        lines.append(f"• {d.get('title', d['slug'])} — {d.get('retailer', '')}")
        lines.append(f"  {prices}")
        lines.append(f"  Deal: {deal_url}")
        if d.get("retailer_url"):
            lines.append(f"  At the retailer: {d['retailer_url']}")
        lines.append("")
        items.append(
            "<li><strong>{title}</strong> — {retailer}<br>{prices}<br>"
            '<a href="{deal}">View the deal</a> · '
            '<a href="{retail}">At the retailer</a></li>'.format(
                title=html.escape(str(d.get("title", d["slug"]))),
                retailer=html.escape(str(d.get("retailer", ""))),
                prices=html.escape(prices), deal=html.escape(deal_url),
                retail=html.escape(str(d.get("retailer_url", "")))))
    lines.append(f"Tracked hourly by Espresso Deal Alerts — {BASE}")
    text = "\n".join(lines)

    page = ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(heading)}</title></head><body>"
            f"<h1>☕ {html.escape(heading)}</h1><ul>{''.join(items)}</ul>"
            f"<p>Tracked hourly by <a href=\"{BASE}\">Espresso Deal Alerts</a>.</p>"
            "</body></html>")
    return text, page


def send_telegram(token, chat_id, text):
    """POST one message via the Telegram Bot API. Returns True on success.

    Kept as a small function over urllib.request.urlopen so tests can
    monkeypatch the transport — the real API is never called in tests.
    """
    if len(text) > TELEGRAM_TEXT_LIMIT:
        text = text[:TELEGRAM_TEXT_LIMIT - 40].rstrip() + \
            f"\n… more drops on the site: {BASE}"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API returned not-ok: {body.get('description')}")
    return True


def find_drops_path():
    env = os.environ.get("ALERT_DROPS_PATH")
    if env:
        return Path(env)
    for cand in (Path("drops.json"), Path("../drops.json")):
        if cand.exists():
            return cand
    return Path("drops.json")  # missing-file path: load handles it


def load_drops(path):
    """Return the drops list, or None on missing/malformed input."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log(f"drops file not found at {path} — nothing to alert, exiting.")
        return None
    except Exception as e:
        log(f"drops file at {path} is malformed ({e}) — nothing to alert, exiting.")
        return None
    drops = data.get("drops") if isinstance(data, dict) else None
    if not isinstance(drops, list):
        log("drops file has no 'drops' list — nothing to alert, exiting.")
        return None
    good, skipped = [], 0
    for d in drops:
        if valid_drop(d):
            good.append(d)
        else:
            skipped += 1
    if skipped:
        log(f"skipped {skipped} malformed drop entr{'y' if skipped == 1 else 'ies'}.")
    return good


def load_state(path):
    """Return (state_dict, is_first_run). Malformed state is treated as an
    empty (but existing) state — never as a first run, so a corrupt file
    cannot trigger a re-seed + duplicate-alert cycle silently; it logs."""
    if not path.exists():
        return {"version": 1, "sent": []}, True
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("sent"), list):
            raise ValueError("unexpected shape")
        return state, False
    except Exception as e:
        log(f"WARNING: state file {path} unreadable ({e}); starting from an "
            "empty sent-set. Previously alerted drops may re-alert once.")
        return {"version": 1, "sent": []}, False


def save_state(path, state):
    state["sent"] = sorted(set(state["sent"]))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)  # atomic-ish: never leave a half-written state file


def write_outbox(outbox_dir, drops, text, page_html, when):
    """Write the email-ready digest copy. Returns the file path."""
    outbox_dir.mkdir(parents=True, exist_ok=True)
    keys = ",".join(sorted(drop_key(d) for d in drops))
    digest = hashlib.sha256(keys.encode("utf-8")).hexdigest()[:10]
    fname = f"{when.strftime('%Y-%m-%d')}-{digest}.md"
    path = outbox_dir / fname
    path.write_text(
        f"# Espresso Deal Alerts digest — {when.strftime('%Y-%m-%d')}\n\n"
        "Email-ready copy of the price-drop digest (plain text below, HTML "
        "version in the fenced block). Written on every run that finds new "
        "drops, whether or not Telegram credentials are configured.\n\n"
        "## Plain text\n\n```\n" + text + "\n```\n\n"
        "## HTML\n\n```html\n" + page_html + "\n```\n",
        encoding="utf-8")
    return path


def main():
    drops_path = find_drops_path()
    state_path = Path(os.environ.get("ALERT_STATE_PATH", "alert-state.json"))
    outbox_dir = Path(os.environ.get("ALERT_OUTBOX_DIR", "alerts-outbox"))
    now = datetime.now(timezone.utc)

    drops = load_drops(drops_path)
    if drops is None:
        return 0

    state, first_run = load_state(state_path)

    if first_run:
        # Baseline seeding: record everything currently in drops.json as
        # already-seen and send nothing (see module docstring).
        state["seeded_at"] = now.isoformat().replace("+00:00", "Z")
        state["sent"] = [drop_key(d) for d in drops]
        save_state(state_path, state)
        log(f"first run: baseline seeded with {len(drops)} existing "
            "drop(s) marked already-seen; no alerts sent. New drops from "
            "the next run onward will alert.")
        return 0

    sent_keys = set(state["sent"])
    new_drops = [d for d in drops if drop_key(d) not in sent_keys]
    if not new_drops:
        log(f"no new drops ({len(drops)} in feed, all already alerted/seeded).")
        return 0

    log(f"{len(new_drops)} new drop(s) to alert.")
    text, page_html = format_digest(new_drops, now)
    outbox_path = write_outbox(outbox_dir, new_drops, text, page_html, now)
    log(f"digest written to outbox: {outbox_path}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("alerts pending: no Telegram credentials configured "
            "(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set). State NOT "
            "updated — these drops will alert on the first run after "
            "credentials are added.")
        return 0

    try:
        send_telegram(token, chat_id, text)
    except Exception as e:
        log(f"Telegram send FAILED ({e}). State NOT updated — will retry "
            "on the next run.")
        return 0

    state["sent"].extend(drop_key(d) for d in new_drops)
    state["last_alert_at"] = now.isoformat().replace("+00:00", "Z")
    save_state(state_path, state)
    log(f"Telegram digest sent ({len(new_drops)} drop(s)); state updated "
        f"({len(state['sent'])} keys total).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
