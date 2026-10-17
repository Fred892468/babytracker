import csv
import hmac
import io
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, request, send_from_directory, session

DB_PATH = os.environ.get("DB_PATH", "/data/tracker.db")
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Rome"))
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
BABY_NAME = os.environ.get("BABY_NAME", "la bimba")

KINDS = {"cacca", "pipi", "entrambi"}
CONSISTENCIES = {"liquida", "molle", "normale", "dura"}
COLORS = {"giallo", "verde", "marrone", "nero", "rosso", "bianco"}

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=90),
    MAX_CONTENT_LENGTH=16 * 1024,
)

if not APP_PASSWORD or not SECRET_KEY:
    raise SystemExit("APP_PASSWORD e SECRET_KEY devono essere impostate")
if len(APP_PASSWORD) < 8:
    raise SystemExit("APP_PASSWORD deve avere almeno 8 caratteri")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,            -- UTC ISO 8601
            kind TEXT NOT NULL,
            consistency TEXT,
            color TEXT,
            note TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(ts)")
    conn.commit()
    conn.close()


init_db()

# --- auth -------------------------------------------------------------------

_failures: dict[str, list[float]] = {}
# Limite per IP (5) e limite globale (30) in 15 minuti: l'IP dietro proxy e' falsificabile
# via X-Forwarded-For, il limite globale impedisce comunque il brute force.


def too_many_failures(ip: str) -> bool:
    now = time.time()
    for key in list(_failures):
        _failures[key] = [t for t in _failures[key] if now - t < 900]
        if not _failures[key]:
            del _failures[key]
    total = sum(len(v) for v in _failures.values())
    return len(_failures.get(ip, [])) >= 5 or total >= 30


@app.post("/api/login")
def login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if too_many_failures(ip):
        return jsonify(error="Troppi tentativi, riprova tra 15 minuti"), 429
    data = request.get_json(silent=True) or {}
    password = str(data.get("password", ""))
    name = str(data.get("name", "")).strip()[:30]
    if not name:
        return jsonify(error="Inserisci il tuo nome"), 400
    if not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        _failures.setdefault(ip, []).append(time.time())
        return jsonify(error="Password errata"), 401
    session.clear()
    session.permanent = True
    session["name"] = name
    return jsonify(name=name)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


def require_login():
    if not session.get("name"):
        return jsonify(error="Non autenticato"), 401
    return None


@app.before_request
def guard():
    if request.path.startswith("/api/") and request.path not in ("/api/login", "/api/logout"):
        return require_login()
    if request.path == "/export.csv":
        return require_login()
    return None


@app.after_request
def headers(resp):
    resp.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else resp.headers.get("Cache-Control", "")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp

# --- helpers ----------------------------------------------------------------


def parse_ts(value) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc)


def row_to_dict(r):
    return {k: r[k] for k in ("id", "ts", "kind", "consistency", "color", "note", "author")}

# --- API --------------------------------------------------------------------


@app.get("/api/me")
def me():
    return jsonify(name=session["name"], baby=BABY_NAME)


@app.get("/api/entries")
def list_entries():
    try:
        days = max(1, min(int(request.args.get("days", 14)), 365))
    except ValueError:
        days = 14
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = db().execute(
        "SELECT * FROM entries WHERE ts >= ? ORDER BY ts DESC LIMIT 2000", (since,)
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/entries")
def add_entry():
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    if kind not in KINDS:
        return jsonify(error="Tipo non valido"), 400
    consistency = data.get("consistency") or None
    color = data.get("color") or None
    if kind == "pipi":
        consistency = color = None
    if consistency is not None and consistency not in CONSISTENCIES:
        return jsonify(error="Consistenza non valida"), 400
    if color is not None and color not in COLORS:
        return jsonify(error="Colore non valido"), 400
    try:
        ts = parse_ts(data["ts"]) if data.get("ts") else datetime.now(timezone.utc)
    except ValueError:
        return jsonify(error="Data non valida"), 400
    if ts > datetime.now(timezone.utc) + timedelta(minutes=5):
        return jsonify(error="La data e' nel futuro"), 400
    note = str(data.get("note", "")).strip()[:500]
    cur = db().execute(
        "INSERT INTO entries (ts, kind, consistency, color, note, author, created_at) VALUES (?,?,?,?,?,?,?)",
        (ts.isoformat(), kind, consistency, color, note, session["name"], datetime.now(timezone.utc).isoformat()),
    )
    db().commit()
    row = db().execute("SELECT * FROM entries WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.delete("/api/entries/<int:entry_id>")
def delete_entry(entry_id):
    cur = db().execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    db().commit()
    if cur.rowcount == 0:
        return jsonify(error="Non trovato"), 404
    return jsonify(ok=True)


@app.get("/api/stats")
def stats():
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=14)).isoformat()
    rows = db().execute(
        "SELECT ts, kind, color FROM entries WHERE ts >= ? ORDER BY ts", (since,)
    ).fetchall()
    today = now.astimezone(TZ).date()
    per_day = {(today - timedelta(days=i)).isoformat(): {"cacca": 0, "pipi": 0} for i in range(13, -1, -1)}
    for r in rows:
        day = datetime.fromisoformat(r["ts"]).astimezone(TZ).date().isoformat()
        if day not in per_day:
            continue
        if r["kind"] in ("cacca", "entrambi"):
            per_day[day]["cacca"] += 1
        if r["kind"] in ("pipi", "entrambi"):
            per_day[day]["pipi"] += 1
    last = db().execute(
        "SELECT ts FROM entries WHERE kind IN ('cacca','entrambi') ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    hours_since = None
    if last:
        hours_since = round((now - datetime.fromisoformat(last["ts"])).total_seconds() / 3600, 1)
    recent_alert = db().execute(
        "SELECT ts, color FROM entries WHERE color IN ('rosso','nero','bianco') AND ts >= ? ORDER BY ts DESC LIMIT 1",
        ((now - timedelta(days=3)).isoformat(),),
    ).fetchone()
    return jsonify(
        per_day=[{"date": d, **v} for d, v in per_day.items()],
        hours_since_poop=hours_since,
        alert_color=dict(recent_alert) if recent_alert else None,
    )


@app.get("/export.csv")
def export_csv():
    rows = db().execute("SELECT * FROM entries ORDER BY ts").fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["data_ora", "tipo", "consistenza", "colore", "note", "inserito_da"])
    for r in rows:
        local = datetime.fromisoformat(r["ts"]).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
        # prefisso ' per evitare che Excel/Sheets interpretino note e nomi come formule
        safe = lambda t: "'" + t if t[:1] in ("=", "+", "-", "@") else t
        w.writerow([local, r["kind"], r["consistency"] or "", r["color"] or "", safe(r["note"]), safe(r["author"])])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=diario-pannolino.csv"},
    )


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return "ok"
