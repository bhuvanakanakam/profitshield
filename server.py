#!/usr/bin/env python3
"""ProfitShield backend.

A small, dependency-free server (Python standard library only):
  - SQLite database of orders, error charges and supplier invoice lines
  - Importers for payout / POS / invoice CSV exports, with column mapping:
        known export formats  ->  instant
        unfamiliar columns    ->  AI maps them (OpenRouter), rules as fallback
  - Square POS sync over the Square sandbox API only
  - AI endpoints: grounded Q&A (/api/ask) and receipt photo reading (/api/extract)

    export OPENROUTER_API_KEY=sk-or-...          # AI features
    export SQUARE_ACCESS_TOKEN=EAAA...           # optional, Square sandbox token
    python3 server.py                            # http://localhost:8000
"""
import csv
import io
import copy
import hashlib
import math
import secrets
import socket
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
import prototype
import legacy_finance
from financial_engine import price_increment, commission_overcharge, cents as financial_cents
from square_actions import ActionError, PlanStore, fingerprint

from input_security import (BODY_LIMIT, ROW_LIMIT, CHANNELS, UNITS, POST_ROUTES, EXPENSIVE_ROUTES,
                            RequestLimiter, validate_origin, validate_payload, bounded_json, number,
                            text as checked_text, invoice_line, validate_tool_args, iso_date)

ROOT = Path(__file__).resolve().parent


def _load_env(path):
    """Read KEY=value lines from .env (never committed: it's in .gitignore). Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")
SAMPLES = ROOT / "samples"
DB_PATH = Path(os.environ.get("BACKHOUSE_DB", ROOT / "backhouse.db"))
KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
EXTERNAL_AI_APPROVED = os.environ.get("PROFITSHIELD_AI_PRIVACY_APPROVED", "").lower() == "true"
MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
VISION_MODEL = os.environ.get("OPENROUTER_VISION_MODEL", MODEL)
SQUARE_TOKEN = os.environ.get("SQUARE_ACCESS_TOKEN", "").strip()
# Environment configuration cannot select a production endpoint.
SQUARE_BASE = "https://connect.squareupsandbox.com"
SQUARE_VERSION = os.environ.get("SQUARE_VERSION", "2024-10-17")
TZ = ZoneInfo(os.environ.get("STORE_TZ", "America/New_York"))
PORT = int(os.environ.get("PORT", "8000"))
LOCK = threading.Lock()
MUTATION_LOCK = threading.Lock()
ACTION_CONTEXT = threading.local()
PUBLIC_ORIGIN = validate_origin(os.environ.get("PROFITSHIELD_ORIGIN", f"http://localhost:{PORT}"))
REQUEST_LIMITER = RequestLimiter()

# Store catalog. Mirrors MENU / ING in index.html (names are what exports print).
MENU = {
    "marg": ("Margherita pizza", 16.0), "pep": ("Pepperoni pizza", 18.0), "knots": ("Garlic knots", 7.0),
    "caesar": ("Caesar salad", 11.0), "wings": ("Buffalo wings (10)", 15.0), "soda": ("Soda (2 L)", 4.5),
    "tira": ("Tiramisu", 8.0),
}
MENU_ALIASES = {"margherita": "marg", "pepperoni": "pep", "knots": "knots", "garlic knot": "knots", "caesar": "caesar",
                "wing": "wings", "soda": "soda", "coke": "soda", "pepsi": "soda", "sprite": "soda", "tiramisu": "tira"}
INGREDIENTS = {
    "dough": ("Dough balls", "ea", ["DOUGH"]), "mozz": ("Mozzarella", "lb", ["MOZZ"]),
    "sauce": ("Pizza sauce", "lb", ["SAUCE PIZZA", "PIZZA SAUCE"]), "pepperoni": ("Pepperoni", "lb", ["PEPPERONI"]),
    "basil": ("Fresh basil", "oz", ["BASIL"]), "parm": ("Parmesan", "lb", ["PARM"]),
    "romaine": ("Romaine", "lb", ["ROMAINE"]), "gbutter": ("Garlic butter", "lb", ["BUTTER GARLIC", "GARLIC BUTTER"]),
    "dressing": ("Caesar dressing", "lb", ["DRSG CAESAR", "CAESAR DRESSING"]), "croutons": ("Croutons", "lb", ["CROUTON"]),
    "wings": ("Chicken wings", "lb", ["WING JMBO", "CHIX WING", "CHICKEN WING"]),
    "buffalo": ("Buffalo sauce", "lb", ["BUFFALO"]), "soda": ("Soda 2 L", "ea", ["SODA 2L"]),
    "tira": ("Tiramisu portion", "ea", ["TIRAMISU"]),
}

# ---------------------------------------------------------------- formats
PAYOUT_FIELDS = ["order_id", "date", "time", "datetime", "subtotal", "commission", "promo_amount", "promo_name",
                 "error_amount", "error_reason", "error_item", "items"]
POS_FIELDS = ["order_id", "date", "time", "datetime", "subtotal", "items"]
INVOICE_FIELDS = ["invoice_number", "vendor", "date", "source", "line", "description", "qty", "unit", "unit_price", "total"]

KNOWN_FORMATS = [
    {"name": "DoorDash payout report", "kind": "payout", "channel": "DoorDash", "map": {
        "order_id": "DoorDash Order ID", "date": "Order Date", "time": "Order Time", "subtotal": "Subtotal",
        "commission": "Commission", "promo_amount": "Merchant Funded Promotion", "promo_name": "Promotion Name",
        "error_amount": "Error Charges", "error_reason": "Error Reason", "error_item": "Error Item", "items": "Items"}},
    {"name": "Uber Eats payment report", "kind": "payout", "channel": "Uber Eats", "map": {
        "order_id": "Order ID", "date": "Date", "time": "Time", "subtotal": "Sales (excl. tax)",
        "commission": "Uber Service Fee", "promo_amount": "Promotions on items", "promo_name": "Offer Name",
        "error_amount": "Order Error Adjustments", "error_reason": "Adjustment Reason", "error_item": "Adjusted Item",
        "items": "Order Details"}},
    {"name": "Grubhub transaction report", "kind": "payout", "channel": "Grubhub", "map": {
        "order_id": "Order Number", "date": "Transaction Date", "time": "Transaction Time", "subtotal": "Food & Beverage",
        "commission": "Commission", "promo_amount": "Restaurant-Funded Promotion", "promo_name": "Promotion",
        "error_amount": "Adjustment", "error_reason": "Adjustment Reason", "error_item": "Adjustment Item",
        "items": "Order Items"}},
    {"name": "Square orders export", "kind": "pos", "channel": "In-store", "map": {
        "order_id": "Order", "date": "Date", "time": "Time", "subtotal": "Gross Sales", "items": "Itemization"}},
    {"name": "Supplier invoice lines", "kind": "invoice", "channel": None, "map": {
        "invoice_number": "Invoice Number", "vendor": "Vendor", "date": "Invoice Date", "source": "Received Via",
        "line": "Line", "description": "Description", "qty": "Qty", "unit": "Unit", "unit_price": "Unit Price",
        "total": "Extended"}},
]

SYNONYMS = {
    "order_id": r"order.?(id|number|no|#)|txn|transaction.?(id|#)|^order$|^id$",
    "datetime": r"when|timestamp|date.?time",
    "date": r"date|day",
    "time": r"^time|hour",
    "subtotal": r"subtotal|food|sales|gross|basket.?total|order.?value",
    "commission": r"commission|fee|cut|service",
    "promo_amount": r"promo.*\$|promotion(?!.*name)|discount|offer.*amount|marketing",
    "promo_name": r"promo.*(desc|name)|offer.?name|campaign",
    "error_amount": r"adj.*\$|error.*(charge|amount)|adjust(ment)?s?$|refund",
    "error_reason": r"adj.*note|reason",
    "error_item": r"adj.*item|error.?item|adjusted.?item",
    "items": r"items|basket$|details|itemization|order.?items",
}


def channel_from_name(name):
    n = (name or "").lower()
    for key, ch in [("doordash", "DoorDash"), ("uber", "Uber Eats"), ("grubhub", "Grubhub"), ("square", "In-store"),
                    ("pos", "In-store")]:
        if key in n:
            return ch
    return None


# ---------------------------------------------------------------- database
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with LOCK, db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS orders(channel TEXT, order_id TEXT, date TEXT, time TEXT, subtotal REAL,
          commission REAL, promo_name TEXT, promo_amount REAL, items TEXT, source TEXT, PRIMARY KEY(channel, order_id));
        CREATE TABLE IF NOT EXISTS errors(channel TEXT, order_id TEXT, amount REAL, reason TEXT, item TEXT,
          PRIMARY KEY(channel, order_id));
        CREATE TABLE IF NOT EXISTS invoice_lines(invoice_number TEXT, vendor TEXT, date TEXT, source TEXT, line INTEGER,
          description TEXT, ingredient TEXT, qty REAL, unit TEXT, unit_price REAL, total REAL, ai INTEGER DEFAULT 0,
          PRIMARY KEY(invoice_number, vendor, source, line));
        CREATE TABLE IF NOT EXISTS square_actions(id TEXT PRIMARY KEY, kind TEXT, review TEXT,
          snapshot TEXT, created REAL, status TEXT, result TEXT);
        CREATE TABLE IF NOT EXISTS item_map(alias TEXT PRIMARY KEY, sku TEXT);
        CREATE TABLE IF NOT EXISTS ingredient_map(description TEXT PRIMARY KEY, ingredient TEXT);
        CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, file TEXT, kind TEXT,
          channel TEXT, format TEXT, mapped_by TEXT, rows INTEGER, new_rows INTEGER, mapping TEXT, redacted INTEGER DEFAULT 0);
        """)
        for stmt in ("ALTER TABLE imports ADD COLUMN redacted INTEGER DEFAULT 0",
                     "ALTER TABLE invoice_lines ADD COLUMN page INTEGER"):
            try:
                con.execute(stmt)
            except sqlite3.OperationalError:
                pass
        con.execute("CREATE TABLE IF NOT EXISTS menu_prices(sku TEXT PRIMARY KEY, price REAL, updated TEXT, pushed TEXT)")
        prototype.initialize(con)
        empty = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    if empty:
        seed()


def seed():
    for f in sorted(SAMPLES.glob("*.csv")):
        if "unusual" in f.name:
            continue  # kept back for the live "unfamiliar columns" demo
        res = import_csv(f.read_text(encoding="utf-8-sig"), f.name, allow_ai=False)
        print(f"  seeded {f.name}: {res['new_rows']} rows ({res['format']})")


def reset():
    with LOCK:
        if DB_PATH.exists():
            DB_PATH.unlink()
    init_db()


# ---------------------------------------------------------------- parsing helpers
def num(v):
    if v is None:
        return 0.0
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise ValueError("Invalid numeric value")
    s = str(v).strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        x = float(s) if s else 0.0
    except ValueError:
        raise ValueError("Invalid numeric value") from None
    if not math.isfinite(x) or abs(x) > 1e12:
        raise ValueError("Numeric value must be finite and within range")
    return -x if neg else x


def parse_date(s):
    if s is not None and not isinstance(s, str):
        raise ValueError("Date must be text")
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%b %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    if s:
        raise ValueError("Invalid date")
    return None


def parse_time(s):
    s = (s or "").strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            return datetime.strptime(s, fmt).strftime("%H:%M")
        except ValueError:
            pass
    if s:
        raise ValueError("Invalid time")
    return "12:00"


def split_datetime(s):
    s = (s or "").strip().replace("T", " ")
    m = re.match(r"(.+?)\s+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp][Mm])?)", s)
    if m:
        return parse_date(m.group(1)), parse_time(m.group(2))
    return parse_date(s), "12:00"


def learned(con, table, key):
    r = con.execute(f"SELECT {'sku' if table == 'item_map' else 'ingredient'} FROM {table} WHERE "
                    f"{'alias' if table == 'item_map' else 'description'}=?", (key,)).fetchone()
    return r[0] if r else None


def match_sku(con, name):
    n = name.strip()
    low = n.lower()
    hit = learned(con, "item_map", low)
    if hit:
        return hit
    for sku, (mname, _) in MENU.items():
        if mname.lower() == low:
            return sku
    for alias, sku in MENU_ALIASES.items():
        if alias in low:
            return sku
    return None


def parse_items(con, text):
    out = []
    for part in re.split(r"[;|\n]", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[xX×]?\s+(.+)$", part) or re.match(r"^(.+?)\s*[xX×]\s*(\d+)$", part)
        if m and m.group(1).isdigit():
            qty, name = int(m.group(1)), m.group(2)
        elif m:
            name, qty = m.group(1), int(m.group(2))
        else:
            qty, name = 1, part
        number(qty, 'item quantity', minimum=1, maximum=1e6, integer=True)
        out.append({"sku": match_sku(con, name), "qty": qty, "name": name.strip()})
    return out


def match_ingredient(con, desc):
    hit = learned(con, "ingredient_map", desc.upper())
    if hit:
        return hit
    up = desc.upper()
    for key, (_, _, kws) in INGREDIENTS.items():
        if any(k in up for k in kws):
            return key
    return None


# ---------------------------------------------------------------- privacy
PII_HEADER = re.compile(r"customer|guest|employee|cashier|server name|staff|driver|dasher|courier|e-?mail|phone|"
                        r"address|^name$|first.?name|last.?name|card|member", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}")


def redact(headers, rows):
    """Drop personal columns and scrub emails / phone numbers from the rest, before storage or any AI call."""
    pii_cols = [h for h in headers if PII_HEADER.search(h or "")]
    keep = [h for h in headers if h not in pii_cols]
    count = 0
    clean = []
    for r in rows:
        out = {}
        for h in keep:
            v = r.get(h)
            if isinstance(v, str) and (EMAIL.search(v) or PHONE.search(v)) and not re.fullmatch(r"[\d.,$()\s-]+", v):
                v = PHONE.sub("[phone]", EMAIL.sub("[email]", v))
                count += 1
            out[h] = v
        count += sum(1 for h in pii_cols if (r.get(h) or "").strip())
        clean.append(out)
    return keep, clean, count, pii_cols


# ---------------------------------------------------------------- column mapping
def detect_known(headers):
    hs = set(h.strip() for h in headers)
    for f in KNOWN_FORMATS:
        if all(v in hs for v in f["map"].values()):
            return f
    return None


def map_by_rules(headers, kind):
    fields = PAYOUT_FIELDS if kind == "payout" else POS_FIELDS
    used, mp = set(), {}
    for field in ["error_item", "error_reason", "error_amount", "promo_name", "promo_amount", "order_id", "datetime",
                  "date", "time", "subtotal", "commission", "items"]:
        if field not in fields:
            continue
        for h in headers:
            if h in used:
                continue
            if re.search(SYNONYMS[field], h.strip().lower()):
                mp[field] = h
                used.add(h)
                break
    if "datetime" in mp and "date" in mp and mp["date"] == mp["datetime"]:
        mp.pop("date")
    return mp


def map_by_ai(headers, sample_rows, filename):
    prompt = {
        "task": "Map the columns of a restaurant sales export to canonical fields.",
        "canonical_fields": {
            "order_id": "unique order id", "date": "order date", "time": "order time",
            "datetime": "date and time in one column", "subtotal": "food and beverage sales before tax and fees",
            "commission": "platform commission or service fee charged to the restaurant",
            "promo_amount": "promotion cost paid by the restaurant", "promo_name": "promotion name",
            "error_amount": "refund or error charge billed to the restaurant", "error_reason": "reason for that charge",
            "error_item": "item the charge was for", "items": "list of items ordered"},
        "filename": filename, "headers": headers, "sample_rows": sample_rows[:3],
        "return": {"channel": "DoorDash | Uber Eats | Grubhub | In-store | unknown",
                   "kind": "payout (delivery app) | pos (in-store sales)",
                   "mapping": {"<canonical_field>": "<exact header or null>"}},
    }
    out = parse_json(call_openrouter(MODEL, [
        {"role": "system", "content": "You map spreadsheet columns. Return only JSON. Use exact header strings."},
        {"role": "user", "content": json.dumps(prompt)}], json_mode=True))
    mp = {k: v for k, v in (out.get("mapping") or {}).items() if v in headers}
    return mp, out.get("channel"), out.get("kind")


# ---------------------------------------------------------------- import
def import_csv(text, filename, channel=None, allow_ai=True):
    validate_payload('/api/import', {'text': text, 'filename': filename, 'channel': channel}, MENU, INGREDIENTS)
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    if not headers or len(headers) > 100 or any(not h or len(h) > 100 for h in headers) or len(set(headers)) != len(headers):
        raise ValueError("CSV requires distinct, nonempty headers (up to 100)")
    reader.fieldnames = headers
    rows = []
    try:
        for row in reader:
            if len(rows) >= ROW_LIMIT:
                raise ValueError("CSV exceeds 20000 rows")
            if None in row or any(v is None or len(v) > 4096 for v in row.values()):
                raise ValueError("CSV rows must match headers and cells must be at most 4096 characters")
            rows.append(row)
    except csv.Error:
        raise ValueError("Invalid CSV") from None
    headers, rows, redacted, pii_cols = redact(headers, rows)
    fmt = detect_known(headers)
    mapped_by, fmt_name = "known format", None
    if fmt:
        kind, ch, mp, fmt_name = fmt["kind"], fmt["channel"], dict(fmt["map"]), fmt["name"]
    else:
        ch = channel or channel_from_name(filename)
        kind = "pos" if ch == "In-store" else "payout"
        mp, ai_ch, ai_kind = None, None, None
        if allow_ai and KEY:
            try:
                mp, ai_ch, ai_kind = map_by_ai(headers, rows, filename)
                mapped_by = "AI"
            except Exception as e:  # fall back to rules
                print("AI mapping failed:", e)
                mp = None
        if not mp:
            mp, mapped_by = map_by_rules(headers, kind), "rules"
        ch = ch or (ai_ch if ai_ch in ("DoorDash", "Uber Eats", "Grubhub", "In-store") else None)
        if ai_kind in ("payout", "pos") and not fmt:
            kind = "pos" if ch == "In-store" else ai_kind
        fmt_name = "Unfamiliar layout"
        if not ch:
            raise ValueError("couldn't tell which app this file is from. Put the app name in the file name")
        need = ["order_id", "subtotal"] + ([] if "datetime" in mp else ["date"])
        missing = [f for f in need if f not in mp]
        if missing:
            raise ValueError("couldn't find columns for " + ", ".join(missing))

    get = lambda r, f: (r.get(mp[f]) if f in mp and mp[f] else None)
    new = 0
    with LOCK, db() as con:
        if kind == "invoice":
            for r in rows:
                inv, vendor = (get(r, "invoice_number") or "").strip(), (get(r, "vendor") or "").strip()
                d = parse_date(get(r, "date"))
                if not inv or not d:
                    raise ValueError("Invoice number and valid date are required")
                checked_text(inv, 'invoice number', 200, required=True)
                checked_text(vendor, 'vendor', 200, required=True)
                unit = (get(r, "unit") or "ea").strip()
                ingredient = match_ingredient(con, get(r, "description") or "")
                invoice_line({'description': get(r, "description") or "", 'ingredient': ingredient,
                              'qty': num(get(r, "qty")), 'unit': unit, 'unit_price': num(get(r, "unit_price")),
                              'total': num(get(r, "total"))}, INGREDIENTS)
                number(num(get(r, "line")), 'line', minimum=1, maximum=100000, integer=True)
                desc = (get(r, "description") or "").strip()
                cur = con.execute("INSERT OR IGNORE INTO invoice_lines(invoice_number, vendor, date, source, line, description,"
                                  " ingredient, qty, unit, unit_price, total, ai) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)", (
                    inv, vendor, d, (get(r, "source") or "CSV import").strip(), int(num(get(r, "line"))),
                    desc, match_ingredient(con, desc), num(get(r, "qty")), (get(r, "unit") or "ea").strip(),
                    num(get(r, "unit_price")), num(get(r, "total"))))
                new += cur.rowcount
        else:
            for r in rows:
                oid = (get(r, "order_id") or "").strip()
                if "datetime" in mp:
                    d, t = split_datetime(get(r, "datetime"))
                else:
                    d, t = parse_date(get(r, "date")), parse_time(get(r, "time"))
                if not oid or not d:
                    raise ValueError("Order id and valid date are required")
                checked_text(oid, 'order id', 255, required=True)
                items = parse_items(con, get(r, "items") or "")
                cur = con.execute("INSERT OR IGNORE INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    ch, oid, d, t, abs(num(get(r, "subtotal"))), abs(num(get(r, "commission"))),
                    (get(r, "promo_name") or "").strip() or None, abs(num(get(r, "promo_amount"))),
                    json.dumps(items), fmt_name))
                new += cur.rowcount
                amt = abs(num(get(r, "error_amount")))
                if amt > 0:
                    con.execute("INSERT OR IGNORE INTO errors VALUES(?,?,?,?,?)", (
                        ch, oid, amt, (get(r, "error_reason") or "Adjustment").strip(), (get(r, "error_item") or "").strip()))
        con.execute("INSERT INTO imports(ts,file,kind,channel,format,mapped_by,rows,new_rows,mapping,redacted) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (datetime.now(TZ).isoformat(timespec="seconds"), filename, kind, ch, fmt_name, mapped_by, len(rows),
                     new, json.dumps(mp), redacted))
    return {"file": filename, "kind": kind, "channel": ch, "format": fmt_name, "mapped_by": mapped_by,
            "rows": len(rows), "new_rows": new, "duplicates_skipped": len(rows) - new, "mapping": mp,
            "redacted": redacted, "removed_columns": pii_cols}


def save_invoice(p):
    validate_payload('/api/invoices', p, MENU, INGREDIENTS)
    lines = p.get("lines") or []
    d = parse_date(p.get("date") or "") or datetime.now(TZ).date().isoformat()
    inv = (p.get("invoice_number") or "").strip() or "PHOTO-" + uuid.uuid4().hex[:6].upper()
    vendor = (p.get("vendor") or "Unknown vendor").strip()
    src = "PDF invoice" if str(p.get("filename", "")).lower().endswith(".pdf") else "Receipt photo"
    with LOCK, db() as con:
        for i, l in enumerate(lines, 1):
            ing = l.get("ingredient") or None
            desc = str(l.get("description", ""))[:120]
            con.execute("INSERT OR REPLACE INTO invoice_lines(invoice_number, vendor, date, source, line, description, ingredient,"
                        " qty, unit, unit_price, total, ai, page) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)", (
                inv, vendor, d, src, i, desc, ing, num(l.get("qty")), l.get("unit") or "ea",
                num(l.get("unit_price")), round(num(l.get("qty")) * num(l.get("unit_price")), 2),
                int(l["page"]) if str(l.get("page") or "").isdigit() else None))
            if ing and desc:  # remember the owner's confirmed match for next time
                con.execute("INSERT OR REPLACE INTO ingredient_map VALUES(?,?)", (desc.upper(), ing))
        con.execute("INSERT INTO imports(ts,file,kind,channel,format,mapped_by,rows,new_rows,mapping) VALUES(?,?,?,?,?,?,?,?,?)",
                    (datetime.now(TZ).isoformat(timespec="seconds"), f"{vendor} {inv}", "invoice", None,
                     src, "AI + owner", len(lines), len(lines), "{}"))
    return {"ok": True, "invoice_number": inv, "date": d}


# ---------------------------------------------------------------- data for the app
def monday(d):
    return d - timedelta(days=d.weekday())


def week_label(start):
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}"
    return f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}"


def app_data():
    with LOCK, db() as con:
        orders = [dict(r) for r in con.execute("SELECT * FROM orders ORDER BY date, time")]
        errors = [dict(r) for r in con.execute("SELECT * FROM errors")]
        lines = [dict(r) for r in con.execute("SELECT * FROM invoice_lines ORDER BY date, invoice_number, source, line")]
        imports = [dict(r) for r in con.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 40")]
        menu_prices = {r["sku"]: {"price": r["price"], "updated": r["updated"], "pushed": json.loads(r["pushed"] or "{}")}
                       for r in con.execute("SELECT * FROM menu_prices")}
    starts = sorted({monday(date.fromisoformat(o["date"])) for o in orders})
    weeks = [{"id": s.isoformat(), "start": s.isoformat(), "label": week_label(s)} for s in starts]
    wk = lambda d: monday(date.fromisoformat(d)).isoformat()
    by_order = {}
    out_orders = []
    for o in orders:
        d = date.fromisoformat(o["date"])
        h, m = (int(x) for x in o["time"].split(":")[:2])
        sub = o["subtotal"]
        rec = {"id": o["order_id"], "channel": o["channel"], "week": wk(o["date"]), "date": o["date"],
               "dow": d.weekday(), "hour": h, "minute": m, "items": json.loads(o["items"] or "[]"),
               "subtotal": sub, "commission": o["commission"], "rate": (o["commission"] / sub) if sub else 0,
               "promo": o["promo_name"], "promoAmt": o["promo_amount"] or 0, "source": o["source"]}
        by_order[(o["channel"], o["order_id"])] = rec
        out_orders.append(rec)
    out_errors = []
    for i, e in enumerate(errors, 1):
        o = by_order.get((e["channel"], e["order_id"]))
        if not o:
            continue
        sku = None
        if e["item"]:
            with db() as con:
                sku = match_sku(con, e["item"])
        out_errors.append({"id": f"EC-{7100 + i}", "orderId": e["order_id"], "channel": e["channel"], "week": o["week"],
                           "date": o["date"], "dow": o["dow"], "hour": o["hour"], "sku": sku, "item": e["item"],
                           "reason": e["reason"], "amount": e["amount"]})
    invs = {}
    for l in lines:
        k = (l["invoice_number"], l["vendor"], l["source"])
        inv = invs.setdefault(k, {"id": l["invoice_number"], "vendor": l["vendor"], "date": l["date"], "week": wk(l["date"]),
                                  "source": l["source"], "ai": bool(l["ai"]), "lines": [], "total": 0})
        inv["lines"].append({"desc": l["description"], "ing": l["ingredient"], "qty": l["qty"], "unit": l["unit"],
                             "unitPrice": l["unit_price"], "total": l["total"], "page": l.get("page")})
        inv["total"] = round(inv["total"] + l["total"], 2)
    invoices = sorted(invs.values(), key=lambda i: (i["date"], i["source"] != "Supplier portal export" and
                                                    i["source"] != "Emailed PDF"))
    result = {"menu_prices": menu_prices, "weeks": weeks, "orders": out_orders, "errors": out_errors, "invoices": invoices, "imports": imports,
            "square": {"configured": bool(SQUARE_TOKEN), "env": "sandbox"}}
    result["financial_model"] = legacy_finance.model(result)
    return result


# ---------------------------------------------------------------- Square POS (real API)
class NoSquareRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ActionError("Square redirects are forbidden", 502)


SQUARE_OPENER = urllib.request.build_opener(NoSquareRedirect())


def sandbox_guard():
    if SQUARE_BASE != "https://connect.squareupsandbox.com":
        raise ActionError("Only the fixed Square sandbox endpoint is permitted", 403)


def require_action(kind):
    plan = getattr(ACTION_CONTEXT, 'plan', None)
    if not plan or plan['kind'] != kind:
        raise ActionError("A reviewed, claimed sandbox plan is required")
    return plan


def square(method, path, body=None):
    sandbox_guard()
    if not SQUARE_TOKEN:
        raise ActionError("Connect a Square sandbox token first", 400)
    allowed = {('GET', '/v2/locations'), ('POST', '/v2/orders/search'),
               ('POST', '/v2/catalog/search'), ('POST', '/v2/orders'), ('POST', '/v2/catalog/object')}
    if (method, path) not in allowed:
        raise ActionError("Unsupported Square endpoint", 400)
    if path == '/v2/orders':
        require_action('seed')
    if path == '/v2/catalog/object':
        require_action('menu')
    req = urllib.request.Request("https://connect.squareupsandbox.com" + path, method=method,
                                 data=json.dumps(body, allow_nan=False).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {SQUARE_TOKEN}", "Content-Type": "application/json",
                                          "Square-Version": SQUARE_VERSION})
    try:
        with SQUARE_OPENER.open(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise ActionError(f"Square sandbox request failed ({e.code}); inspect sandbox before reviewing another plan", 502) from None
    except (urllib.error.URLError, TimeoutError):
        raise ActionError("Square sandbox outcome is uncertain; inspect it before reviewing another plan", 502) from None


def square_location():
    locs = square("GET", "/v2/locations").get("locations", [])
    if not locs:
        raise RuntimeError("no Square locations on this account")
    return locs[0]["id"], locs[0].get("name", "")


def square_seed():
    """Only the confirmed plan executor may create the reviewed sandbox orders."""
    plan = require_action('seed')
    loc = plan['review']['location']['id']
    baskets = plan['review']['baskets']
    for i, basket in enumerate(baskets):
        square("POST", "/v2/orders", {"idempotency_key": plan['id'] + '-' + str(i), "order": {
            "location_id": loc, "line_items": [{"name": MENU[s][0], "quantity": str(q),
                "base_price_money": {"amount": plan['review']['unit_prices'][s], "currency": "USD"}} for s, q in basket]}})
    return {"created": len(baskets)}


def square_sync():
    loc, loc_name = square_location()
    start = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(timespec="seconds").replace("+00:00", "Z")
    res = square("POST", "/v2/orders/search", {"location_ids": [loc], "limit": 500, "query": {
        "filter": {"date_time_filter": {"created_at": {"start_at": start}}},
        "sort": {"sort_field": "CREATED_AT", "sort_order": "DESC"}}})
    new = 0
    with LOCK, db() as con:
        for o in res.get("orders", []):
            ts = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00")).astimezone(TZ)
            items, sub = [], 0.0
            for li in o.get("line_items", []):
                q = int(float(li.get("quantity", "1")))
                price = (li.get("base_price_money") or {}).get("amount", 0) / 100
                sub += q * price
                items.append({"sku": match_sku(con, li.get("name", "")), "qty": q, "name": li.get("name", "")})
            cur = con.execute("INSERT OR IGNORE INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)", (
                "In-store", "SQ-" + o["id"][:8], ts.date().isoformat(), ts.strftime("%H:%M"), round(sub, 2), 0.0,
                None, 0.0, json.dumps(items), "Square API"))
            new += cur.rowcount
        con.execute("INSERT INTO imports(ts,file,kind,channel,format,mapped_by,rows,new_rows,mapping) VALUES(?,?,?,?,?,?,?,?,?)",
                    (datetime.now(TZ).isoformat(timespec="seconds"), f"Square location {loc_name}", "pos", "In-store",
                     "Square Orders API", "API", len(res.get("orders", [])), new, "{}"))
    return {"pulled": len(res.get("orders", [])), "new_rows": new, "location": loc_name}


def catalog_target(name):
    res = square("POST", "/v2/catalog/search", {"object_types": ["ITEM"], "query": {
        "exact_query": {"attribute_name": "name", "attribute_value": name}}})
    objects = res.get('objects') or []
    if res.get('cursor') or len(objects) > 1:
        raise ActionError("Catalog match is ambiguous; resolve it in the sandbox before review")
    if not objects:
        return {'mode': 'create', 'name': name}
    variations = objects[0].get('item_data', {}).get('variations', [])
    if len(variations) != 1:
        raise ActionError("Review requires exactly one catalog variation per item")
    variation = variations[0]
    money = variation.get('item_variation_data', {}).get('price_money', {})
    if (not isinstance(variation.get('id'), str) or type(variation.get('version')) is not int or
            money.get('currency') != 'USD' or type(money.get('amount')) is not int or money['amount'] < 0):
        raise ActionError("Unsupported sandbox catalog variation")
    return {'mode': 'update', 'name': name, 'variation': variation}


def square_set_price(name, cents):
    plan = require_action('menu')
    change = next((c for c in plan['review']['changes'] if c['item'] == name), None)
    if not change or cents != change['cents'] or not plan['review']['connected']:
        raise ActionError("Price does not match the reviewed plan")
    target = change['target']
    if target['mode'] == 'update':
        obj = copy.deepcopy(target['variation'])
        obj['item_variation_data']['pricing_type'] = 'FIXED_PRICING'
        obj['item_variation_data']['price_money'] = {'amount': cents, 'currency': 'USD'}
    else:
        obj = {'type': 'ITEM', 'id': '#item', 'item_data': {'name': name, 'variations': [{
            'type': 'ITEM_VARIATION', 'id': '#var', 'item_variation_data': {
                'name': 'Regular', 'pricing_type': 'FIXED_PRICING',
                'price_money': {'amount': cents, 'currency': 'USD'}}}]}}
    square('POST', '/v2/catalog/object', {'idempotency_key': plan['id'] + '-' + change['sku'], 'object': obj})
    return 'updated' if target['mode'] == 'update' else 'created'


def action_state(con):
    # Exclude the action ledger: making a second review is not itself a data change.
    records = {table: [tuple(row) for row in con.execute('SELECT * FROM '+table+' ORDER BY rowid')]
               for table in ('orders', 'errors', 'invoice_lines', 'menu_prices', 'item_map', 'ingredient_map')}
    return fingerprint({'data': records, 'sandbox': SQUARE_BASE,
                        'connection': hashlib.sha256(SQUARE_TOKEN.encode()).hexdigest(), 'menu': MENU})


PLANS = PlanStore(db)


def cents(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1e6 or not math.isfinite(value):
        raise ActionError("Prices must be finite positive numbers up to 1,000,000", 400)
    rounded = int(round(value * 100))
    if abs(value * 100 - rounded) > .000001:
        raise ActionError("Prices must use at most two decimal places", 400)
    return rounded


def preview_menu(p):
    sandbox_guard()
    if not isinstance(p, dict) or set(p) != {'changes', 'channels', 'week'}:
        raise ActionError("Supply changes, channels and selected week", 400)
    if not isinstance(p['channels'], list) or not 1 <= len(p['channels']) <= 4 or any(
            ch not in ('In-store', 'DoorDash', 'Uber Eats', 'Grubhub') for ch in p['channels']):
        raise ActionError("Select valid channels", 400)
    if len(set(p['channels'])) != len(p['channels']):
        raise ActionError("Repeated channels", 400)
    if not isinstance(p['week'], str):
        raise ActionError("A valid selected week is required", 400)
    try:
        week = date.fromisoformat(p['week'])
    except ValueError:
        raise ActionError("A valid selected week is required", 400) from None
    if week.isoformat() != p['week'] or week.weekday() != 0:
        raise ActionError("Select an ISO Monday week", 400)
    changes = p['changes']
    if not isinstance(changes, list) or not 1 <= len(changes) <= len(MENU):
        raise ActionError("Review requires a bounded nonempty change list", 400)
    with MUTATION_LOCK, LOCK, db() as con:
        if not con.execute('SELECT 1 FROM orders WHERE date BETWEEN ? AND ?',
                           (week.isoformat(), (week+timedelta(days=6)).isoformat())).fetchone():
            raise ActionError("Selected week has no restaurant records", 400)
        saved = {r['sku']: r['price'] for r in con.execute('SELECT * FROM menu_prices')}
        seen, reviewed = set(), []
        for change in changes:
            if not isinstance(change, dict) or set(change) != {'sku', 'price', 'old_price'}:
                raise ActionError("Each reviewed change needs sku, old_price and price", 400)
            sku = change['sku']
            if not isinstance(sku, str) or sku not in MENU or sku in seen:
                raise ActionError("Unknown or repeated menu item", 400)
            seen.add(sku)
            new, old = cents(change['price']), cents(change['old_price'])
            if old != cents(saved.get(sku, MENU[sku][1])):
                raise ActionError("Simulation price is stale; simulate and review again")
            if new == old:
                raise ActionError("Price must change", 400)
            reviewed.append({'sku': sku, 'item': MENU[sku][0], 'old_price': old/100,
                             'new_price': new/100, 'cents': new})
        connected = bool(SQUARE_TOKEN) and 'In-store' in p['channels']
        for change in reviewed:
            change['target'] = catalog_target(change['item']) if connected else {'mode': 'manual'}
        review = {'week': p['week'], 'channels': p['channels'], 'changes': reviewed,
                  'connected': connected, 'environment': 'sandbox',
                  'notice': 'Square writes are sandbox only. Delivery apps require manual portal edits. '
                            'Local reference prices will update; this does not prove a live menu was changed.'}
        state = action_state(con)
    return PLANS.create('menu', review, state)


def preview_seed(p):
    sandbox_guard()
    if p != {}:
        raise ActionError("Seed review expects an empty JSON object", 400)
    loc, name = square_location()
    review = {'location': {'id': loc, 'name': name}, 'environment': 'sandbox',
              'unit_prices': {sku: int(round(value[1]*100)) for sku, value in MENU.items()},
              'baskets': [[['pep', 2], ['soda', 1]], [['marg', 1], ['knots', 1]],
                          [['wings', 1], ['caesar', 1], ['soda', 2]]],
              'notice': 'Creates exactly 3 sandbox test orders. No production orders are created.'}
    with LOCK, db() as con:
        state = action_state(con)
    return PLANS.create('seed', review, state)


def execute_action(p, kind):
    sandbox_guard()
    with MUTATION_LOCK:
        with LOCK:
            plan = PLANS.claim(p, kind, action_state)
        try:
            review = plan['review']
            if kind == 'seed':
                loc, name = square_location()
                if {'id': loc, 'name': name} != review['location']:
                    raise ActionError("Sandbox location changed; review again")
            elif review['connected']:
                for change in review['changes']:
                    if catalog_target(change['item']) != change['target']:
                        raise ActionError("Sandbox catalog changed; review again")
            ACTION_CONTEXT.plan = plan
            if kind == 'seed':
                result = square_seed()
                result['sync'] = square_sync()
            else:
                results = []
                # Every target was checked before the first external write.
                for change in review['changes']:
                    pushed = {}
                    if 'In-store' in review['channels']:
                        pushed['Square POS'] = ('sandbox price ' + square_set_price(change['item'], change['cents']) +
                                                ' via Catalog API') if review['connected'] else 'not connected; no Square write'
                    for channel in review['channels']:
                        if channel != 'In-store':
                            pushed[channel] = 'manual merchant portal change required'
                    with LOCK, db() as con:
                        con.execute('INSERT OR REPLACE INTO menu_prices VALUES(?,?,?,?)',
                                    (change['sku'], change['new_price'], datetime.now(TZ).isoformat(timespec='seconds'), json.dumps(pushed)))
                    results.append({**{k: change[k] for k in ('sku', 'item', 'old_price', 'new_price')}, 'pushed': pushed})
                result = {'results': results}
            PLANS.finish(plan, 'completed', result)
            return {'plan_id': plan['id'], 'environment': 'sandbox', **result}
        except Exception:
            PLANS.finish(plan, 'failed')
            raise
        finally:
            ACTION_CONTEXT.plan = None


def apply_menu(p):
    return execute_action(p, 'menu')


def confirm_seed(p):
    return execute_action(p, 'seed')


# ---------------------------------------------------------------- AI
URL = "https://openrouter.ai/api/v1/chat/completions"

ASK_SYSTEM = """You are ProfitShield, a money assistant for a restaurant owner.

Rules:
- Answer ONLY from the FACTS JSON. Never invent numbers, orders, vendors or dates.
- Use dollar figures exactly as they appear in FACTS. Only do simple sums of given figures.
- When you use a finding, cite it inline as [F1], [F2] and so on, using the finding ids in FACTS.
- If FACTS does not contain the answer, say what you can't see and which source would tell you.
- Write like a sharp restaurant accountant talking to a busy owner: plain words, short paragraphs, no jargon.
- Lead with the direct answer in one sentence, then the why, then what to do. Under 150 words unless asked.
- ProfitShield prepares disputes and credit requests; the owner files them in their own portal. Never claim anything was sent.
"""

EXTRACT_SYSTEM = """You read photos and scans of restaurant supplier invoices and receipts.
Return ONLY a JSON object:
{"vendor": string|null, "invoice_number": string|null, "date": "YYYY-MM-DD"|null, "total": number|null,
 "lines": [{"description": exact printed line text, "qty": number, "unit": "lb"|"oz"|"ea"|as printed,
            "unit_price": number per that unit, "total": number,
            "ingredient": one key from INGREDIENTS or null, "confidence": 0..1, "page": page number the line is on (1 for photos)}]}
For multi-page documents, read every page.
When the line matches an ingredient, convert qty and unit_price into that ingredient's unit
(for example "2 @ 35.70" of a 6 LB pack is qty 12, unit lb, unit_price 5.95).
Lower the confidence for blurry, abbreviated or guessed lines. Skip tax, subtotal, payment and total lines.
Never invent lines you cannot read."""


def call_openrouter(model, messages, json_mode=False, temperature=0.2):
    if not EXTERNAL_AI_APPROVED:
        raise ValueError("External AI disabled until an owner-approved privacy policy permits transmission")
    body = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "X-Title": "ProfitShield"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:400]
        if json_mode and e.code == 400:
            return call_openrouter(model, messages, json_mode=False, temperature=temperature)
        raise RuntimeError(f"OpenRouter {e.code}: {detail}")
    return data["choices"][0]["message"]["content"] or ""


def parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("model did not return JSON")
        return json.loads(m.group(0))


# ---- agent tools: each one is plain code over the database; the model only decides which to call
CONTRACT = {"DoorDash": .20, "Uber Eats": .22, "Grubhub": .18, "In-store": 0.0}
APPROVED = {("DoorDash", "Free garlic knots on $30+")}


def _range(week):
    s = date.fromisoformat(week)
    return s.isoformat(), (s + timedelta(days=6)).isoformat()


def _fid(facts, kind):
    for f in facts.get("findings", []):
        if f.get("kind") == kind:
            return f.get("id")
    return None


def tool_profit_bridge(ctx, **_):
    data = app_data()['financial_model']
    current = data['metrics'].get(ctx['week'])
    previous = data['metrics'].get(ctx.get('prev_week'))
    return {'summary': current['baseline'] if current else None,
            'previous': previous['baseline'] if previous else None,
            'assumptions': data['assumptions'], '_chart': 'bridge'}


def tool_check_commissions(ctx, channel=None, **_):
    a, b = _range(ctx["week"])
    out = []
    with db() as con:
        for r in con.execute("SELECT channel, order_id, date, subtotal, commission FROM orders WHERE date BETWEEN ? AND ? "
                             "AND channel != 'In-store'", (a, b)):
            if channel and r["channel"] != channel:
                continue
            c = CONTRACT.get(r['channel'], 0)
            excess = commission_overcharge({'gross_sales': financial_cents(r['subtotal']),
                'commissions': financial_cents(r['commission']), 'contract_rate': str(c),
                'contract_basis': 'gross_sales'})
            if excess:
                out.append({'channel': r['channel'], 'order': r['order_id'], 'date': r['date'],
                            'charged_rate': round(r['commission'] / r['subtotal'], 3), 'contract_rate': c,
                            'overcharge': excess / 100})
    return {"overcharged_orders": len(out), "total_overcharge": sum(financial_cents(x["overcharge"]) for x in out) / 100,
            "contract_rates": CONTRACT, "examples": out[:5], "finding_id": _fid(ctx["facts"], "comm")}


def tool_find_unapproved_promos(ctx, **_):
    a, b = _range(ctx["week"])
    agg = {}
    with db() as con:
        for r in con.execute("SELECT channel, promo_name, promo_amount FROM orders WHERE date BETWEEN ? AND ? AND promo_name "
                             "IS NOT NULL AND promo_amount > 0", (a, b)):
            k = (r["channel"], r["promo_name"])
            x = agg.setdefault(k, {"channel": k[0], "promotion": k[1], "orders": 0, "cost": 0.0, "approved": k in APPROVED})
            x["orders"] += 1
            x["cost"] = (financial_cents(x["cost"]) + financial_cents(r["promo_amount"])) / 100
    return {"promotions": list(agg.values()), "finding_id": _fid(ctx["facts"], "promo")}


def _prices(con, week):
    a, b = _range(week)
    acc, seen = {}, set()
    for r in con.execute("SELECT * FROM invoice_lines WHERE date BETWEEN ? AND ? AND ingredient IS NOT NULL "
                         "ORDER BY source", (a, b)):
        key = (r["invoice_number"], r["vendor"], r["line"])
        if key in seen:
            continue
        seen.add(key)
        x = acc.setdefault(r["ingredient"], [0.0, 0.0, r["vendor"]])
        x[0] += r["qty"]
        x[1] += r["qty"] * r["unit_price"]
    return {k: (v[1] / v[0], v[2]) for k, v in acc.items() if v[0]}


def tool_find_price_changes(ctx, min_pct=5, **_):
    with db() as con:
        cur, prev = _prices(con, ctx["week"]), _prices(con, ctx["prev_week"])
    out = []
    for k, (p, vendor) in cur.items():
        if k in prev and prev[k][0]:
            ch = (p - prev[k][0]) / prev[k][0] * 100
            if abs(ch) >= float(min_pct):
                out.append({"ingredient": INGREDIENTS[k][0], "vendor": vendor, "unit": INGREDIENTS[k][1],
                            "last_week": round(prev[k][0], 2), "this_week": round(p, 2), "change_pct": round(ch, 1)})
    return {"changes": sorted(out, key=lambda x: -abs(x["change_pct"])), "finding_id": _fid(ctx["facts"], "price")}


def tool_find_duplicate_invoices(ctx, **_):
    with db() as con:
        rows = con.execute("SELECT invoice_number, vendor, date, GROUP_CONCAT(DISTINCT source) AS sources, "
                           "COUNT(DISTINCT source) AS copies, SUM(total) / COUNT(DISTINCT source) AS amount "
                           "FROM invoice_lines GROUP BY invoice_number, vendor HAVING copies > 1").fetchall()
    return {"duplicates": [dict(r) for r in rows], "finding_id": _fid(ctx["facts"], "dup")}


def tool_error_charge_pattern(ctx, **_):
    a, b = _range(ctx["week"])
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT e.*, o.date, o.time FROM errors e JOIN orders o USING(channel, order_id) "
                                             "WHERE o.date BETWEEN ? AND ?", (a, b))]
    by_item, peak = {}, 0
    for r in rows:
        by_item[r["item"] or "whole order"] = by_item.get(r["item"] or "whole order", 0) + 1
        d, h = date.fromisoformat(r["date"]), int(r["time"][:2])
        peak += d.weekday() in (4, 5) and 18 <= h <= 20
    return {"charges": len(rows), "total": sum(financial_cents(r["amount"]) for r in rows) / 100, "by_item": by_item,
            "fri_sat_6_to_9pm": peak, "finding_id": _fid(ctx["facts"], "err")}


def tool_menu_margins(ctx, **_):
    model = app_data()['financial_model']
    return {'food_cost_estimates': model['menu_costs'].get(ctx['week'], {}),
            'assumptions': model['assumptions']}


def tool_simulate_price_change(ctx, items=None, increase=0.5, channels=None, **_):
    names = {k: v[0] for k, v in MENU.items()}
    wanted = set()
    for it in items or ["Margherita pizza", "Pepperoni pizza"]:
        sku = it if it in MENU else next((k for k, n in names.items() if it.lower() in n.lower()), None)
        if sku:
            wanted.add(sku)
    chans = channels or ["DoorDash", "Uber Eats", "Grubhub", "In-store"]
    a, b = _range(ctx["week"])
    units = {}
    with db() as con:
        for r in con.execute("SELECT channel, items FROM orders WHERE date BETWEEN ? AND ?", (a, b)):
            if r["channel"] not in chans:
                continue
            for it in json.loads(r["items"] or "[]"):
                if it.get("sku") in wanted:
                    units[r["channel"]] = units.get(r["channel"], 0) + it["qty"]
    inc = float(increase)
    per_cents = price_increment(units, increase, CONTRACT)
    per = {ch: amount / 100 for ch, amount in per_cents.items()}
    leak = -sum(f.get("weekly_impact", 0) for f in ctx["facts"].get("findings", []) if f.get("kind") == "price")
    return {"items": [names[k] for k in wanted], "increase_per_item": inc, "channels": chans,
            "units_sold_last_week": units, "extra_profit_per_week_by_channel": per,
            "extra_profit_per_week": sum(per_cents.values()) / 100, "price_increase_leak_per_week": round(leak, 2),
            "assumes": "same order volume as last week (no demand drop)", "_chart": "sim"}


TOOLS = {
    "get_profit_bridge": (tool_profit_bridge, "Break down why profit changed from last week to this week, step by step.", {}),
    "check_commissions": (tool_check_commissions, "Find delivery orders where the commission charged is above the contract rate.",
                          {"channel": {"type": "string", "description": "DoorDash, Uber Eats or Grubhub; omit for all"}}),
    "find_unapproved_promos": (tool_find_unapproved_promos, "List restaurant-funded promotions this week and whether each was approved.", {}),
    "find_price_changes": (tool_find_price_changes, "Compare ingredient prices on supplier invoices this week vs last week.",
                           {"min_pct": {"type": "number", "description": "minimum % change to report, default 5"}}),
    "find_duplicate_invoices": (tool_find_duplicate_invoices, "Find duplicate invoice documents; documents do not prove duplicate payment.", {}),
    "error_charge_pattern": (tool_error_charge_pattern, "Summarize missing/incorrect item charges this week by item and time.", {}),
    "menu_margins": (tool_menu_margins, "Food cost and margin per menu item, last week vs this week.", {}),
    "simulate_price_change": (tool_simulate_price_change, "Estimate extra weekly profit from raising prices on some items.",
                              {"items": {"type": "array", "items": {"type": "string"}, "description": "menu item names"},
                               "increase": {"type": "number", "description": "dollars added per item"},
                               "channels": {"type": "array", "items": {"type": "string"},
                                            "description": "DoorDash, Uber Eats, Grubhub, In-store"}}),
}
TOOL_SPECS = [{"type": "function", "function": {"name": n, "description": d, "parameters": {
    "type": "object", "properties": props}}} for n, (_, d, props) in TOOLS.items()]

AGENT_SYSTEM = ASK_SYSTEM + """
You have tools that run exact calculations over the owner's records. Call the tools you need before answering;
use FACTS only for context and finding ids. Quote numbers from tool results. When a tool result has finding_id, cite it.
For "what if" questions use simulate_price_change and state its assumption."""


def ask(p):
    validate_payload('/api/ask', p, MENU, INGREDIENTS)
    if not EXTERNAL_AI_APPROVED:
        raise ValueError('External AI disabled until privacy approval; use the deterministic Guru')
    facts = p.get("facts", {})
    ctx = {"facts": facts, "week": p.get("week") or facts.get("week_start"), "prev_week": p.get("prev_week")}
    # Numerical facts come from SQLite and the canonical engine, never browser assertions.
    source_model = app_data()['financial_model']
    current = source_model['metrics'].get(ctx['week'])
    previous = source_model['metrics'].get(ctx['prev_week'])
    facts = {**facts, 'summary': current['baseline'] if current else None,
             'profit_bridge': {'current': current['baseline'] if current else None,
                               'previous': previous['baseline'] if previous else None},
             'findings': [{'id': f.get('id'), 'kind': f.get('kind')} for f in facts.get('findings', [])]}
    ctx['facts'] = facts
    msgs = [{"role": "system", "content": AGENT_SYSTEM},
            {"role": "user", "content": "FACTS (findings and labels):\n" + json.dumps(
                {k: facts.get(k) for k in ("store", "this_week", "last_week", "summary", "findings")})}]
    for h in (p.get("history") or [])[-6:-1]:
        if h.get("role") in ("user", "assistant"):
            msgs.append({"role": h["role"], "content": str(h.get("content", ""))[:2000]})
    msgs.append({"role": "user", "content": p.get("question", "")})
    trace, chart = [], None
    for _ in range(5):
        body = {"model": MODEL, "messages": msgs, "tools": TOOL_SPECS, "temperature": 0.2}
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "X-Title": "ProfitShield"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                msg = json.load(r)["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"OpenRouter {e.code}: {e.read().decode(errors='ignore')[:300]}")
        calls = msg.get("tool_calls") or []
        if not calls:
            answer = (msg.get("content") or "").strip()
            ids = {f.get("id") for f in facts.get("findings", [])}
            return {"answer": answer, "cites": [c for c in dict.fromkeys(re.findall(r"F\d+", answer)) if c in ids],
                    "tools": trace, "chart": chart}
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            try:
                arguments = c['function'].get('arguments') or '{}'
                checked_text(arguments, 'tool arguments', 8000)
                args = json.loads(arguments)
                validate_tool_args(name, args, MENU)
                res = TOOLS[name][0](ctx, **args) if name in TOOLS else {"error": "unknown tool"}
            except Exception as e:
                res = {"error": str(e)}
            if isinstance(res, dict) and res.get("_chart"):
                chart = {"type": res.pop("_chart"), "data": res}
            trace.append(name)
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(res)[:6000]})
    return {"answer": "I ran out of steps on that one. Try asking a narrower question.", "cites": [], "tools": trace,
            "chart": chart}


def extract(p):
    validate_payload('/api/extract', p, MENU, INGREDIENTS)
    image = p.get("image", "")
    if not image.startswith("data:"):
        raise ValueError("send the file as a data URL")
    is_pdf = image.startswith("data:application/pdf")
    part = ({"type": "file", "file": {"filename": p.get("filename") or "invoice.pdf", "file_data": image}} if is_pdf
            else {"type": "image_url", "image_url": {"url": image}})
    ings = [{"key": k, "name": v[0], "unit": v[1], "typical_text": v[2]} for k, v in INGREDIENTS.items()]
    with db() as con:
        known = [dict(r) for r in con.execute("SELECT * FROM ingredient_map LIMIT 200")]
    out = parse_json(call_openrouter(VISION_MODEL, [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": "INGREDIENTS:\n" + json.dumps(ings) + "\nPREVIOUSLY CONFIRMED MATCHES:\n" + json.dumps(known)},
            part]}], json_mode=True))
    bounded_json(out)
    if not isinstance(out, dict) or not isinstance(out.get('lines'), list) or not 1 <= len(out['lines']) <= 500:
        raise ValueError("Invalid extracted invoice")
    lines = []
    for raw in out['lines']:
        if not isinstance(raw, dict):
            raise ValueError("Invalid extracted invoice line")
        qty, price = raw.get('qty'), raw.get('unit_price')
        number(qty, 'qty', minimum=0.000001, maximum=1e6)
        number(price, 'unit_price', maximum=1e6)
        line = {"description": raw.get("description", ""), "qty": qty, "unit": raw.get("unit", "ea"),
                "unit_price": price, "total": raw.get('total', qty * price),
                "ingredient": raw.get('ingredient'), "confidence": raw.get('confidence', 0.5),
                "page": raw.get('page', 1)}
        invoice_line(line, INGREDIENTS)
        lines.append(line)
    for field in ('vendor', 'invoice_number'):
        if out.get(field) is not None:
            checked_text(out[field], field, 200)
    if out.get('date') is not None:
        iso_date(out['date'])
    if out.get('total') is not None:
        number(out['total'], 'invoice total')
    return {"vendor": out.get("vendor"), "invoice_number": out.get("invoice_number"), "date": out.get("date"),
            "total": out.get("total"), "lines": lines}


# ---------------------------------------------------------------- http
# The dashboard embeds its scripts, styles and icons. No repository directory is
# public: add future assets here individually, never via directory serving.
PUBLIC_ASSETS = {"/": "index.html", "/index.html": "index.html"}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)
        self.csp_nonce = secrets.token_urlsafe(24)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'nonce-" + self.csp_nonce +
                         "'; script-src-attr 'none'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                         "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
                         "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if PUBLIC_ORIGIN.startswith('https://'):
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def _limited(self, costly=False):
        if not REQUEST_LIMITER.allow(self.client_address[0], costly=costly):
            self._json(429, {"error": "Request limit reached; retry later"})
            return True
        return False

    def _json(self, code, obj):
        body = json.dumps(obj, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if code == 429:
            self.send_header("Retry-After", "60")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self._limited():
            return
        # Match canonical URL paths only. Do not decode or normalize untrusted
        # paths into filesystem names (including encoded traversal/separators).
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/api/health":
            return self._json(200, {"ok": True, "ai": bool(KEY and EXTERNAL_AI_APPROVED), "model": MODEL, "backend": True,
                                    "square": bool(SQUARE_TOKEN)})
        if path == "/api/prototype":
            with LOCK, db() as con:
                return self._json(200, prototype.output(con))
        if path == "/api/data":
            return self._json(200, app_data())
        asset = PUBLIC_ASSETS.get(path)
        if asset is not None:
            target = ROOT / asset
            # Fail closed if an allowlisted file is replaced with a symlink.
            if target.is_symlink() or not target.is_file():
                return self._json(404, {"error": "not found"})
            page = target.read_text(encoding="utf-8").replace('<script>', '<script nonce="' + self.csp_nonce + '">')
            body = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">"
                    "</head><body>" + page + "</body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self._limited(costly=self.path in EXPENSIVE_ROUTES):
            return
        if self.path not in POST_ROUTES:
            return self._json(404, {"error": "unknown route"})
        if self.headers.get("Origin") != PUBLIC_ORIGIN or self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self._json(403, {"error": "Invalid request origin"})
        if self.headers.get("Content-Type", "").split(';', 1)[0].strip().lower() != "application/json":
            return self._json(415, {"error": "Use application/json"})
        try:
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding'):
                return self._json(413, {"error": "Unsupported body encoding"})
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,10}', lengths[0]):
                raise ValueError("A single nonnegative Content-Length is required")
            n = int(lengths[0])
            review_route = self.path in ('/api/menu/preview', '/api/menu/apply', '/api/square/seed/preview', '/api/square/seed')
            if n > (32768 if review_route else BODY_LIMIT):
                return self._json(413, {"error": "Body exceeds 16 MiB or uses unsupported encoding"})
            raw = self.rfile.read(n)
            if len(raw) != n:
                raise ValueError("Incomplete request body")
            def reject_constant(_):
                raise ValueError("JSON numbers must be finite")
            def distinct_fields(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON field")
                    result[key] = value
                return result
            p = json.loads(raw or b"{}", parse_constant=reject_constant, object_pairs_hook=distinct_fields)
            if self.path == '/api/simulate':
                bounded_json(p)
                if not isinstance(p, dict) or set(p) - {'week', 'items', 'increase', 'channels'}:
                    raise ValueError('Invalid simulation request')
                iso_date(p.get('week'))
                arguments = {k: v for k, v in p.items() if k != 'week'}
                validate_tool_args('simulate_price_change', arguments, MENU)
                context = {'week': p['week'], 'facts': {}}
                return self._json(200, tool_simulate_price_change(context, **arguments))
            if self.path.startswith('/api/prototype/'):
                bounded_json(p)
                with LOCK, db() as con:
                    result = prototype.operate(con, self.path.rsplit('/', 1)[-1], p, KNOWN_FORMATS, parse_date)
                return self._json(200, result)
            validate_payload(self.path, p, MENU, INGREDIENTS)
            if self.path == '/api/menu/preview':
                return self._json(200, preview_menu(p))
            if self.path == '/api/square/seed/preview':
                return self._json(200, preview_seed(p))
            if self.path == "/api/import":
                return self._json(200, import_csv(p.get("text", ""), p.get("filename", "upload.csv"), p.get("channel")))
            if self.path == "/api/invoices":
                return self._json(200, save_invoice(p))
            if self.path == "/api/menu/apply":
                return self._json(200, apply_menu(p))
            if self.path == "/api/reset":
                reset()
                return self._json(200, {"ok": True})
            if self.path == '/api/square/seed':
                return self._json(200, confirm_seed(p))
            if self.path == '/api/square/sync':
                return self._json(200, square_sync())
            if self.path in ("/api/ask", "/api/extract"):
                print(f"  {self.path}: {(p.get('question') or p.get('filename') or '')[:80]}")
                if not KEY:
                    return self._json(400, {"error": "OPENROUTER_API_KEY is not set"})
                return self._json(200, ask(p) if self.path == "/api/ask" else extract(p))
            return self._json(404, {"error": "unknown route"})
        except ActionError as error:
            return self._json(error.status, {'error': str(error)})
        except (ValueError, TypeError, csv.Error, RecursionError, OverflowError):
            return self._json(400, {"error": "Invalid request data; check fields, dates and numeric ranges"})
        except (TimeoutError, socket.timeout):
            return self._json(408, {"error": "Request body timed out"})
        except Exception:
            print(f"  ERROR {self.path}: request failed")
            return self._json(500, {"error": "Request failed"})

    def log_message(self, fmt, *args):
        if args and "/api/" in str(args[0]) and "data" not in str(args[0]):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    init_db()
    print(f"ProfitShield on http://localhost:{PORT}")
    print(f"  AI: {'on, ' + MODEL if KEY else 'off (set OPENROUTER_API_KEY)'}")
    print(f"  Square: {'on, ' + SQUARE_BASE if SQUARE_TOKEN else 'off (set SQUARE_ACCESS_TOKEN for live POS sync)'}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
