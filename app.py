import os
import io
import json
import sqlite3
import hashlib
import requests
import traceback
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response, send_file, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageOps

app = Flask(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
DB_FILE = "mealsync.db"

SYNC_VERSION = 1
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

PANEL_WIDTH = 400
PANEL_HEIGHT = 300
SCALE = 2
CANVAS_W = PANEL_WIDTH * SCALE
CANVAS_H = PANEL_HEIGHT * SCALE

@app.after_request
def add_cors_and_cache_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, HEAD"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

# ============================================================================
# DATABASE SETUP WITH PERSISTENT TELEMETRY TABLE
# ============================================================================
def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_menu (
                day_name TEXT PRIMARY KEY,
                breakfast TEXT,
                lunch TEXT,
                dinner TEXT,
                task1 TEXT,
                task2 TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS device_telemetry (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                battery_pct INTEGER,
                battery_label TEXT,
                voltage REAL,
                rssi INTEGER,
                wifi_strength TEXT,
                last_seen TEXT
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO device_telemetry (id, battery_pct, battery_label, voltage, rssi, wifi_strength, last_seen)
            VALUES (1, 100, '500d+', 4.20, -55, 'Excellent (3/3)', 'Online')
        """)
        conn.commit()

init_db()

def get_telemetry():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM device_telemetry WHERE id = 1").fetchone()
        if row:
            return dict(row)
        return {"battery_pct": 100, "battery_label": "500d+", "voltage": 4.20, "rssi": -55, "wifi_strength": "Excellent (3/3)", "last_seen": "Online"}

def update_telemetry_db(pct, label, v, rssi, wifi_strength):
    now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
    with get_db() as conn:
        conn.execute("""
            INSERT INTO device_telemetry (id, battery_pct, battery_label, voltage, rssi, wifi_strength, last_seen)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                battery_pct = excluded.battery_pct,
                battery_label = excluded.battery_label,
                voltage = excluded.voltage,
                rssi = excluded.rssi,
                wifi_strength = excluded.wifi_strength,
                last_seen = excluded.last_seen
        """, (pct, label, v, rssi, wifi_strength, now_str))
        conn.commit()

# ============================================================================
# TELEMETRY ENDPOINTS
# ============================================================================
@app.route('/api/telemetry', methods=['GET', 'POST', 'OPTIONS'])
def api_telemetry():
    if request.method == 'OPTIONS':
        return Response(status=200)

    if request.method == 'POST':
        try:
            p = request.get_json(force=True)
            pct = int(p.get("pct", 100))
            label = str(p.get("batt", "500d+"))
            v = float(p.get("v", 4.20))
            rssi = int(p.get("rssi", -55))
            wifi_str = str(p.get("wifi_strength", "Good (2/3)"))
            
            update_telemetry_db(pct, label, v, rssi, wifi_str)
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    telem = get_telemetry()
    return jsonify({
        "status": "online",
        "battery_pct": telem["battery_pct"],
        "battery_label": telem["battery_label"],
        "voltage": telem["voltage"],
        "wifi_strength": telem["wifi_strength"],
        "rssi": telem["rssi"],
        "last_seen": telem["last_seen"]
    }), 200

# ============================================================================
# BITMAP RENDERER (Uses Real DB Telemetry)
# ============================================================================
@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    telem = get_telemetry()
    rssi = int(request.args.get('rssi', telem["rssi"]))
    batt_pct = int(request.args.get('pct', telem["battery_pct"]))
    batt_str = str(request.args.get('batt', telem["battery_label"]))

    # Update DB if ESP32 provided params via GET
    if 'rssi' in request.args or 'pct' in request.args:
        v = float(request.args.get('v', telem["voltage"]))
        wifi_lbl = "Excellent (3/3)" if rssi >= -65 else ("Good (2/3)" if rssi >= -78 else "Weak (1/3)")
        update_telemetry_db(batt_pct, batt_str, v, rssi, wifi_lbl)

    # (Rendering and Canvas code remains identical)
    return send_file(buf, mimetype='image/bmp')
