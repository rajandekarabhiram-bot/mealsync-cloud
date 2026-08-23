import os
import io
import json
import sqlite3
import hashlib
import requests
import traceback
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response, send_file, render_template_string, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageOps

app = Flask(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
DB_FILE = "mealsync.db"

# Gemini API Configuration from Render Environment
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# E-Paper Canvas Dimensions (2x supersampled for anti-aliasing)
PANEL_WIDTH = 400
PANEL_HEIGHT = 300
SCALE = 2
CANVAS_W = PANEL_WIDTH * SCALE
CANVAS_H = PANEL_HEIGHT * SCALE

FONT_ENGLISH_PATH = "Rubik-Bold.ttf"
FONT_MARATHI_PATH = "Yantramanav-Bold.ttf"

DEVICE_LOGS = []

# ============================================================================
# 1. DATABASE INITIALIZATION & SEEDING
# ============================================================================
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
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
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM weekly_menu")
        if cur.fetchone()[0] == 0:
            default_days = [
                ("Monday", "पोहे", "वरण भात, पोळी, भेंडी भाजी", "खिचडी, कढी", "दूध आणणे", "उद्यासाठी मटकी भिजवणे"),
                ("Tuesday", "उपमा", "पोळी, मटकी उसळ, भात", "थालीपीठ, लोणी", "किराणा आणणे", "पीठ आंबवणे"),
                ("Wednesday", "इडली, चटणी", "वरण भात, पोळी, वांगी भाजी", "मसाला भात", "भाजी धुणे", "दही लावणे"),
                ("Thursday", "शिरा", "पोळी, शेवभाजी, भात", "मुगाची खिचडी", "कोथिंबीर कापणे", "दूध आणणे"),
                ("Friday", "मेथी पराठा", "वरण भात, फ्लॉवर भाजी, पोळी", "दाल खिचडी", "मेथी निवडून ठेवणे", "पीठ मळणे"),
                ("Saturday", "मिसळ पाव", "पोळी, पनीर भाजी, जीरा राईस", "पावभाजी", "मटार सोलणे", "बटाटे उकडणे"),
                ("Sunday", "डोसा, सांबार", "पुरणपोळी, कटाची आमटी, भजी", "दही भात", "सांबार मसाला", "उद्यासाठी पोहे चाळणे")
            ]
            conn.executemany("INSERT INTO weekly_menu VALUES (?, ?, ?, ?, ?, ?)", default_days)
        conn.commit()

init_db()

# ============================================================================
# 2. AUTO FONT DOWNLOADER
# ============================================================================
def ensure_fonts():
    font_urls = {
        "Rubik-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/rubik/Rubik-Bold.ttf",
        "Yantramanav-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/yantramanav/Yantramanav-Bold.ttf"
    }
    for filename, url in font_urls.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 1000:
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    with open(filename, "wb") as f:
                        f.write(r.content)
            except Exception:
                pass

ensure_fonts()

# ============================================================================
# 3. SCHEDULE & CONTENT HASH (9:00 PM Rollover Logic)
# ============================================================================
def get_target_menu_data():
    now_ist = datetime.now(IST)
    if now_ist.hour >= 21:
        target_date = now_ist + timedelta(days=1)
    else:
        target_date = now_ist

    target_day = target_date.strftime("%A")
    date_str = target_date.strftime("%a, %d %b %Y").upper()

    with get_db() as conn:
        row = conn.execute("SELECT * FROM weekly_menu WHERE day_name = ?", (target_day,)).fetchone()
        if row:
            data = {
                "day": row["day_name"],
                "breakfast": row["breakfast"] or "—",
                "lunch": row["lunch"] or "—",
                "dinner": row["dinner"] or "—",
                "task1": row["task1"] or "—",
                "task2": row["task2"] or "—"
            }
        else:
            data = {
                "day": target_day,
                "breakfast": "—", "lunch": "—", "dinner": "—", "task1": "—", "task2": "—"
            }

    return date_str, data

@app.route('/hash', methods=['GET'])
def get_content_hash():
    date_str, data = get_target_menu_data()
    payload = f"{date_str}|{data['breakfast']}|{data['lunch']}|{data['dinner']}|{data['task1']}|{data['task2']}"
    content_hash = hashlib.md5(payload.encode('utf-8')).hexdigest()[:10]
    return jsonify({"hash": content_hash}), 200

# ============================================================================
# 4. REST APIS FOR MOBILE FRONTEND & GEMINI 3.7 FLASH
# ============================================================================
@app.route('/api/menu', methods=['GET'])
def api_get_menu():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM weekly_menu").fetchall()
        return jsonify([dict(ix) for ix in rows]), 200

@app.route('/api/menu', methods=['POST'])
def api_update_day_menu():
    req = request.get_json(force=True)
    day = req.get("day_name")
    with get_db() as conn:
        conn.execute("""
            UPDATE weekly_menu
            SET breakfast = ?, lunch = ?, dinner = ?, task1 = ?, task2 = ?
            WHERE day_name = ?
        """, (req.get("breakfast"), req.get("lunch"), req.get("dinner"), req.get("task1"), req.get("task2"), day))
        conn.commit()
    return jsonify({"status": "updated"}), 200

@app.route('/api/ai-suggest', methods=['POST'])
def api_ai_suggest():
    req = request.get_json(force=True)
    target_day = req.get("day_name", "Monday")
    user_prompt = req.get("prompt", "Healthy, authentic Maharashtrian pure vegetarian meal with advance prep tasks.")
    api_key = req.get("gemini_key") or GEMINI_API_KEY

    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY environment variable is not configured."}), 400

    with get_db() as conn:
        other_menus = conn.execute("SELECT day_name, lunch, dinner FROM weekly_menu WHERE day_name != ?", (target_day,)).fetchall()
        context_str = "; ".join([f"{r['day_name']}: Lunch={r['lunch']}, Dinner={r['dinner']}" for r in other_menus])

    system_instruction = """
    You are the MealSync AI Sous-Chef and Autonomous Kitchen Planning Engine.
    Design pure-vegetarian meal plans (specializing in Maharashtrian and Indian home cuisine).
    1. Never suggest non-vegetarian food or eggs.
    2. Keep dish names concise (<35 chars) in natural Marathi script.
    3. task1 must be today's immediate kitchen/grocery task in Marathi.
    4. task2 (or advance_prep_alert) must be an overnight/advance preparation task for tomorrow (e.g. soaking lentils/sabudana, sprouting matki, fermenting batter).
    """

    prompt_text = f"""
    Day to plan: {target_day}
    User prompt: {user_prompt}
    Weekly context to avoid repetition: {context_str}

    Return strict JSON format matching this schema:
    {{
      "breakfast": "Marathi morning dish",
      "lunch": "Marathi lunch combo",
      "dinner": "Marathi light dinner",
      "task1": "Today's immediate prep/grocery task in Marathi",
      "task2": "Overnight/advance prep for tomorrow in Marathi",
      "advance_prep_alert": "Overnight/advance prep in Marathi"
    }}
    """

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent?key={api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.4
            }
        }
        res = requests.post(url, json=payload, timeout=12)
        if res.status_code == 200:
            result_json = res.json()
            plan_str = result_json["candidates"][0]["content"]["parts"][0]["text"]
            plan_data = json.loads(plan_str)
            return jsonify(plan_data), 200
        else:
            return jsonify({"error": res.text}), res.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ai-generate-week', methods=['GET', 'POST'])
def auto_plan_full_week():
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    results = {}
    for day in days:
        try:
            with app.test_request_context(json={"day_name": day, "prompt": "Balanced authentic vegetarian home menu"}):
                res, code = api_ai_suggest()
                if code == 200:
                    data = res.get_json()
                    with get_db() as conn:
                        conn.execute("""
                            UPDATE weekly_menu
                            SET breakfast = ?, lunch = ?, dinner = ?, task1 = ?, task2 = ?
                            WHERE day_name = ?
                        """, (data.get("breakfast"), data.get("lunch"), data.get("dinner"), data.get("task1"), data.get("task2") or data.get("advance_prep_alert"), day))
                        conn.commit()
                    results[day] = "generated"
                else:
                    results[day] = "failed"
        except Exception as e:
            results[day] = str(e)
    return jsonify({"status": "week_generation_completed", "details": results}), 200

# ============================================================================
# 5. TELEMETRY LOGGING
# ============================================================================
@app.route('/log', methods=['POST'])
def receive_device_log():
    try:
        log_entry = request.get_json(force=True)
        now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
        log_entry['timestamp'] = now_str
        DEVICE_LOGS.insert(0, log_entry)
        if len(DEVICE_LOGS) > 300:
            DEVICE_LOGS.pop()
        return jsonify({"status": "logged"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/logs', methods=['GET'])
def view_logs():
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MealSync Telemetry Logs</title>
        <meta http-equiv="refresh" content="20">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; }
            table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; margin-top: 16px; }
            th, td { padding: 12px 16px; text-align: left; font-size: 13px; border-bottom: 1px solid #334155; }
            th { background: #334155; color: #cbd5e1; text-transform: uppercase; font-size: 11px; }
            .badge { padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; background: #0284c7; color: white; }
            .wifi-tag { color: #38bdf8; font-weight: 600; }
        </style>
    </head>
    <body>
        <h2>📊 MealSync Hardware Telemetry Logs</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp (IST)</th>
                    <th>Action</th>
                    <th>Battery</th>
                    <th>Voltage</th>
                    <th>Wi-Fi Strength</th>
                    <th>Hash</th>
                    <th>Next Sleep Slot</th>
                </tr>
            </thead>
            <tbody>
                {% for log in logs %}
                <tr>
                    <td>{{ log.timestamp }}</td>
                    <td><span class="badge">{{ log.event }}</span></td>
                    <td><b>{{ log.batt }}</b> ({{ log.pct }}%)</td>
                    <td>{{ log.v }} V</td>
                    <td><span class="wifi-tag">{{ log.wifi_strength }}</span></td>
                    <td><code>{{ log.hash }}</code></td>
                    <td>{{ log.sleep_sec }}s</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </body>
    </html>
    """
    return render_template_string(html_template, logs=DEVICE_LOGS)

# ============================================================================
# 6. E-PAPER BITMAP GENERATION (400x300 Otsu 1-Bit Stream)
# ============================================================================
def safe_font(font_path, size_1x):
    try:
        return ImageFont.truetype(font_path, size_1x * SCALE)
    except Exception:
        return ImageFont.load_default()

def is_ascii(s):
    return all(ord(c) < 128 for c in s)

def get_text_width(font, text):
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * 16

def get_wrapped_lines(text, font, max_width_2x):
    words = str(text).strip().split()
    if not words:
        return []
    lines, curr = [], []
    for w in words:
        test_line = " ".join(curr + [w])
        if get_text_width(font, test_line) <= max_width_2x:
            curr.append(w)
        else:
            if curr:
                lines.append(" ".join(curr))
                curr = [w]
            else:
                lines.append(w)
                curr = []
    if curr:
        lines.append(" ".join(curr))
    return lines

def draw_autofit_text(draw, text_str, x_1x, y_1x, max_w_1x, max_h_1x, max_size=18, min_size=13, max_lines=2, fill_color=0):
    text_str = str(text_str).strip()
    if not text_str:
        return
    font_file = FONT_ENGLISH_PATH if is_ascii(text_str) else FONT_MARATHI_PATH
    selected_font = None
    selected_lines = []
    line_mult = 1.35 if is_ascii(text_str) else 1.40
    
    max_w_2x = max_w_1x * SCALE
    max_h_2x = max_h_1x * SCALE

    for size in range(max_size, min_size - 1, -1):
        test_font = safe_font(font_file, size)
        lines = get_wrapped_lines(text_str, test_font, max_w_2x)
        line_h = int((size * SCALE) * line_mult)
        total_h = len(lines) * line_h
        if len(lines) <= max_lines and total_h <= max_h_2x:
            selected_font = test_font
            selected_lines = lines
            break
            
    if not selected_font:
        selected_font = safe_font(font_file, min_size)
        selected_lines = get_wrapped_lines(text_str, selected_font, max_w_2x)[:max_lines]

    line_h = int((selected_font.size) * line_mult) if hasattr(selected_font, 'size') else 36
    curr_y = y_1x * SCALE
    for line in selected_lines:
        draw.text((x_1x * SCALE, curr_y), line, font=selected_font, fill=fill_color)
        curr_y += line_h

@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        date_str, data = get_target_menu_data()

        rssi = int(request.args.get('rssi', -50))
        batt_str = str(request.args.get('batt', '500d+'))
        batt_pct = int(request.args.get('pct', 100))

        img_2x = Image.new("L", (CANVAS_W, CANVAS_H), 255)
        draw = ImageDraw.Draw(img_2x)

        font_logo = safe_font(FONT_ENGLISH_PATH, 18)
        font_date = safe_font(FONT_ENGLISH_PATH, 13)
        font_badge = safe_font(FONT_ENGLISH_PATH, 13)
        font_section = safe_font(FONT_ENGLISH_PATH, 15)

        draw.rectangle([0, 0, CANVAS_W - 1, 38 * SCALE], fill=0)
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE)
        draw.text((10 * SCALE, 9 * SCALE), "MealSync", font=font_logo, fill=255)

        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
        wifiX, wifiY = 96 * SCALE, 13 * SCALE
        draw.rectangle([wifiX + 4, wifiY + 20, wifiX + 8,  wifiY + 28], fill=255 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 14, wifiY + 12, wifiX + 18, wifiY + 28], fill=255 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 24, wifiY + 4,  wifiX + 28, wifiY + 28], fill=255 if signal_bars >= 3 else 0)

        date_w = get_text_width(font_date, date_str)
        date_center_x = (CANVAS_W - date_w) // 2
        draw.text((date_center_x, 11 * SCALE), date_str, font=font_date, fill=255)

        batX, batY = 362 * SCALE, 12 * SCALE
        draw.rectangle([batX, batY, batX + 24 * SCALE, batY + 14 * SCALE], outline=255, width=SCALE)
        draw.rectangle([batX + 24 * SCALE, batY + 3 * SCALE, batX + 26 * SCALE, batY + 11 * SCALE], fill=255)

        fill_w = max(0, min(20 * SCALE, int((batt_pct / 100.0) * 20 * SCALE)))
        if fill_w > 0:
            draw.rectangle([batX + 2 * SCALE, batY + 2 * SCALE, batX + 2 * SCALE + fill_w, batY + 12 * SCALE], fill=255)

        badge_w = get_text_width(font_badge, batt_str)
        draw.text((batX - badge_w - 10, 11 * SCALE), batt_str, font=font_badge, fill=255)

        sidebar_w = 118 * SCALE
        draw.rectangle([0, 38 * SCALE, sidebar_w, CANVAS_H - 1], fill=0)
        draw.text((10 * SCALE, 52 * SCALE), "BREAKFAST", font=font_section, fill=255)
        draw.text((10 * SCALE, 112 * SCALE), "LUNCH", font=font_section, fill=255)
        draw.text((10 * SCALE, 175 * SCALE), "DINNER", font=font_section, fill=255)
        draw.text((10 * SCALE, 245 * SCALE), "TASKS", font=font_section, fill=255)

        for y_div in [98, 160, 228]:
            draw.line([(0, y_div * SCALE), (sidebar_w, y_div * SCALE)], fill=255, width=2 * SCALE)
            draw.line([(sidebar_w, y_div * SCALE), (CANVAS_W, y_div * SCALE)], fill=0, width=2 * SCALE)

        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)

        draw.rectangle([128 * SCALE, 243 * SCALE, 142 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task1"], 148, 238, 112, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        draw.rectangle([264 * SCALE, 243 * SCALE, 278 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task2"], 284, 238, 110, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        img_downscaled = img_2x.resize((PANEL_WIDTH, PANEL_HEIGHT), resample=Image.Resampling.LANCZOS)
        img_1bit = img_downscaled.point(lambda p: 255 if p > 155 else 0, mode="1")

        if "ESP32" in request.headers.get("User-Agent", ""):
            img_epd = ImageOps.invert(img_1bit.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1")
            return Response(img_epd.tobytes(), mimetype='application/octet-stream')

        buf = io.BytesIO()
        img_1bit.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        traceback.print_exc()
        return f"Internal Error: {err}", 500

# ============================================================================
# 7. PROGRESSIVE WEB APP (SERVED DIRECTLY AT ROOT /)
# ============================================================================
@app.route('/')
def app_home():
    html_ui = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
      <title>MealSync Hub • Smart Kitchen Dashboard</title>
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&family=Yantramanav:wght@500;700&display=swap" rel="stylesheet">
      <script src="https://cdn.tailwindcss.com"></script>
      <script>
        tailwind.config = {
          theme: {
            extend: {
              fontFamily: {
                sans: ['"Plus Jakarta Sans"', 'sans-serif'],
                marathi: ['"Yantramanav"', 'sans-serif'],
              }
            }
          }
        }
      </script>
    </head>
    <body class="bg-slate-50 text-slate-800 font-sans min-h-screen pb-16">

      <!-- Top Navigation -->
      <header class="sticky top-0 z-50 bg-white/90 backdrop-blur-md border-b border-slate-200 px-4 py-3 shadow-sm">
        <div class="max-w-2xl mx-auto flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="text-2xl">🍳</span>
            <span class="font-extrabold text-lg tracking-tight bg-gradient-to-r from-teal-600 to-emerald-600 bg-clip-text text-transparent">MealSync Hub</span>
          </div>
          <button onclick="togglePreviewModal()" class="flex items-center gap-1.5 bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold px-3 py-2 rounded-lg transition-all shadow-sm">
            <span>📱 E-Paper Preview</span>
          </button>
        </div>
      </header>

      <main class="max-w-2xl mx-auto px-4 mt-4 space-y-5">

        <!-- Day Selection Pills -->
        <div class="flex gap-2 overflow-x-auto pb-1 scrollbar-none" id="day-bar">
          <button onclick="selectDay('Monday')" class="day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-teal-600 text-white shadow-md shadow-teal-500/20">Mon</button>
          <button onclick="selectDay('Tuesday')" class="day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600 hover:bg-slate-100">Tue</button>
          <button onclick="selectDay('Wednesday')" class="day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600 hover:bg-slate-100">Wed</button>
          <button onclick="selectDay('Thursday')" class="day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600 hover:bg-slate-100">Thu</button>
          <button onclick="selectDay('Friday')" class="day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600 hover:bg-slate-100">Fri</button>
          <button onclick="selectDay('Saturday')" class="day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600 hover:bg-slate-100">Sat</button>
          <button onclick="selectDay('Sunday')" class="day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600 hover:bg-slate-100">Sun</button>
        </div>

        <!-- AI Sous-Chef Generator Bar -->
        <div class="bg-gradient-to-br from-emerald-500/10 via-teal-500/10 to-indigo-500/10 border border-teal-500/20 rounded-2xl p-4 shadow-sm">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center gap-1.5 text-xs font-bold text-teal-800 uppercase tracking-wider">
              <span>✨ Gemini Sous-Chef</span>
            </div>
            <span id="active-day-label" class="text-xs font-bold text-slate-500">Planning: Monday</span>
          </div>
          <div class="flex gap-2">
            <input id="ai-theme-input" type="text" placeholder="e.g. Traditional fasting, quick post-workout, sprout rich..." class="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs focus:ring-2 focus:ring-teal-500 outline-none text-slate-800" />
            <button onclick="generatePlanForDay()" id="ai-gen-btn" class="bg-teal-600 hover:bg-teal-700 active:scale-95 text-white font-bold text-xs px-4 py-2 rounded-xl transition-all whitespace-nowrap shadow-sm shadow-teal-600/30">
              Generate
            </button>
          </div>
        </div>

        <!-- Meal Input Cards -->
        <div class="space-y-3">
          
          <!-- Breakfast -->
          <div class="bg-white border border-slate-200/80 rounded-2xl p-4 shadow-sm focus-within:border-amber-500 focus-within:ring-2 focus-within:ring-amber-500/20 transition-all">
            <div class="flex items-center gap-2 mb-1.5">
              <span class="w-2.5 h-2.5 rounded-full bg-amber-400"></span>
              <span class="text-[11px] font-extrabold uppercase tracking-wider text-slate-400">Breakfast (नाश्ता)</span>
            </div>
            <input id="input-breakfast" type="text" class="w-full font-marathi text-lg font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. पोहे, चहा / साबुदाणा खिचडी" />
          </div>

          <!-- Lunch -->
          <div class="bg-white border border-slate-200/80 rounded-2xl p-4 shadow-sm focus-within:border-emerald-500 focus-within:ring-2 focus-within:ring-emerald-500/20 transition-all">
            <div class="flex items-center gap-2 mb-1.5">
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
              <span class="text-[11px] font-extrabold uppercase tracking-wider text-slate-400">Lunch (दुपारचे जेवण)</span>
            </div>
            <input id="input-lunch" type="text" class="w-full font-marathi text-lg font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. वरण भात, पोळी, भेंडी भाजी" />
          </div>

          <!-- Dinner -->
          <div class="bg-white border border-slate-200/80 rounded-2xl p-4 shadow-sm focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/20 transition-all">
            <div class="flex items-center gap-2 mb-1.5">
              <span class="w-2.5 h-2.5 rounded-full bg-indigo-500"></span>
              <span class="text-[11px] font-extrabold uppercase tracking-wider text-slate-400">Dinner (रात्रीचे जेवण)</span>
            </div>
            <input id="input-dinner" type="text" class="w-full font-marathi text-lg font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. मुगाची मऊ खिचडी, कढी, पापड" />
          </div>

          <!-- Tasks Grid -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            
            <!-- Task 1 -->
            <div class="bg-white border border-slate-200/80 rounded-2xl p-3.5 shadow-sm focus-within:border-teal-500 focus-within:ring-2 focus-within:ring-teal-500/20 transition-all">
              <div class="flex items-center gap-1.5 mb-1">
                <span class="w-2 h-2 rounded-full bg-teal-500"></span>
                <span class="text-[10px] font-extrabold uppercase tracking-wider text-slate-400">Task 1 (किचन / सामान)</span>
              </div>
              <input id="input-task1" type="text" class="w-full font-marathi text-sm font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. दूध आणणे / कोथिंबीर कापणे" />
            </div>

            <!-- Task 2 (Advance Prep) -->
            <div class="bg-white border border-slate-200/80 rounded-2xl p-3.5 shadow-sm focus-within:border-rose-500 focus-within:ring-2 focus-within:ring-rose-500/20 transition-all">
              <div class="flex items-center gap-1.5 mb-1">
                <span class="w-2 h-2 rounded-full bg-rose-500"></span>
                <span class="text-[10px] font-extrabold uppercase tracking-wider text-rose-500">Task 2 (Advance Prep 🌙)</span>
              </div>
              <input id="input-task2" type="text" class="w-full font-marathi text-sm font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. उद्यासाठी साबुदाणा भिजवणे" />
            </div>

          </div>
        </div>

        <!-- Action Bar -->
        <div class="pt-2">
          <button onclick="saveCurrentDayMenu()" id="save-btn" class="w-full bg-slate-900 hover:bg-slate-800 active:scale-[0.99] text-white font-extrabold text-sm py-3.5 rounded-2xl shadow-lg shadow-slate-900/10 transition-all flex items-center justify-center gap-2">
            <span>💾 Save & Sync E-Paper</span>
          </button>
        </div>

      </main>

      <!-- Live Hardware E-Paper Modal Preview -->
      <div id="preview-modal" class="fixed inset-0 z-50 bg-slate-900/80 backdrop-blur-sm flex items-center justify-center p-4 hidden">
        <div class="bg-white rounded-3xl p-5 max-w-lg w-full shadow-2xl border border-slate-100 space-y-4">
          <div class="flex items-center justify-between pb-2 border-b border-slate-100">
            <div>
              <h3 class="font-extrabold text-slate-900 text-sm">4.2" E-Paper Display Stream</h3>
              <p class="text-[11px] text-slate-400">Live 1-bit bitmap rendered via Render backend</p>
            </div>
            <button onclick="togglePreviewModal()" class="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center text-slate-500 font-bold hover:bg-slate-200">✕</button>
          </div>

          <div class="bg-slate-100 rounded-2xl p-2 flex items-center justify-center overflow-hidden border border-slate-200">
            <img id="epaper-stream-img" src="/display.bmp" alt="E-Paper Stream" class="w-full h-auto rounded-xl shadow-inner border border-slate-300" />
          </div>

          <div class="flex gap-2">
            <button onclick="refreshPreviewImage()" class="flex-1 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-bold py-2.5 rounded-xl transition-all">
              🔄 Refresh Canvas
            </button>
            <a href="/logs" target="_blank" class="flex-1 bg-teal-50 text-teal-700 hover:bg-teal-100 text-xs font-bold py-2.5 rounded-xl text-center transition-all">
              📊 Hardware Logs
            </a>
          </div>
        </div>
      </div>

      <!-- Toast Notification -->
      <div id="toast" class="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 bg-slate-900 text-white text-xs font-bold px-4 py-2.5 rounded-full shadow-xl opacity-0 pointer-events-none transition-all duration-300">
        Menu Saved!
      </div>

      <script>
        let activeDay = "Monday";
        let weeklyMenuCache = {};

        async function loadWeeklySchedule() {
          try {
            const res = await fetch('/api/menu');
            if (res.ok) {
              const data = await res.json();
              data.forEach(item => {
                weeklyMenuCache[item.day_name] = item;
              });
              renderActiveDayInputs();
            }
          } catch (err) {
            showToast("Error connecting to backend database.");
          }
        }

        function selectDay(day) {
          activeDay = day;
          document.getElementById('active-day-label').innerText = `Planning: ${day}`;
          
          document.querySelectorAll('.day-btn').forEach(btn => {
            if (btn.innerText.toLowerCase().startsWith(day.slice(0, 3).toLowerCase())) {
              btn.className = "day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-teal-600 text-white shadow-md shadow-teal-500/20";
            } else {
              btn.className = "day-btn px-4 py-2 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600 hover:bg-slate-100";
            }
          });

          renderActiveDayInputs();
        }

        function renderActiveDayInputs() {
          const item = weeklyMenuCache[activeDay] || { breakfast: '', lunch: '', dinner: '', task1: '', task2: '' };
          document.getElementById('input-breakfast').value = item.breakfast || '';
          document.getElementById('input-lunch').value = item.lunch || '';
          document.getElementById('input-dinner').value = item.dinner || '';
          document.getElementById('input-task1').value = item.task1 || '';
          document.getElementById('input-task2').value = item.task2 || '';
        }

        async function saveCurrentDayMenu() {
          const saveBtn = document.getElementById('save-btn');
          saveBtn.innerText = "Syncing...";
          saveBtn.disabled = true;

          const payload = {
            day_name: activeDay,
            breakfast: document.getElementById('input-breakfast').value.trim(),
            lunch: document.getElementById('input-lunch').value.trim(),
            dinner: document.getElementById('input-dinner').value.trim(),
            task1: document.getElementById('input-task1').value.trim(),
            task2: document.getElementById('input-task2').value.trim()
          };

          try {
            const res = await fetch('/api/menu', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });

            if (res.ok) {
              weeklyMenuCache[activeDay] = payload;
              showToast(`✅ ${activeDay} Synced to E-Paper!`);
            }
          } catch (err) {
            showToast("❌ Failed to save menu.");
          } finally {
            saveBtn.innerText = "💾 Save & Sync E-Paper";
            saveBtn.disabled = false;
          }
        }

        async function generatePlanForDay() {
          const aiBtn = document.getElementById('ai-gen-btn');
          const theme = document.getElementById('ai-theme-input').value.trim();
          aiBtn.innerText = "Cooking...";
          aiBtn.disabled = true;

          try {
            const res = await fetch('/api/ai-suggest', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                day_name: activeDay,
                prompt: theme || "Healthy, authentic Maharashtrian pure vegetarian meal with advance prep tasks."
              })
            });

            if (res.ok) {
              const aiData = await res.json();
              document.getElementById('input-breakfast').value = aiData.breakfast || '';
              document.getElementById('input-lunch').value = aiData.lunch || '';
              document.getElementById('input-dinner').value = aiData.dinner || '';
              document.getElementById('input-task1').value = aiData.task1 || '';
              document.getElementById('input-task2').value = aiData.advance_prep_alert || aiData.task2 || '';
              showToast("✨ AI Menu Generated! Tap Save to apply.");
            } else {
              showToast("Gemini Error. Check API key on Render.");
            }
          } catch (e) {
            showToast("Error contacting Gemini service.");
          } finally {
            aiBtn.innerText = "Generate";
            aiBtn.disabled = false;
          }
        }

        function togglePreviewModal() {
          const modal = document.getElementById('preview-modal');
          modal.classList.toggle('hidden');
          if (!modal.classList.contains('hidden')) {
            refreshPreviewImage();
          }
        }

        function refreshPreviewImage() {
          const img = document.getElementById('epaper-stream-img');
          img.src = `/display.bmp?t=${new Date().getTime()}`;
        }

        function showToast(msg) {
          const toast = document.getElementById('toast');
          toast.innerText = msg;
          toast.classList.remove('opacity-0', 'pointer-events-none');
          setTimeout(() => {
            toast.classList.add('opacity-0', 'pointer-events-none');
          }, 2500);
        }

        loadWeeklySchedule();
      </script>
    </body>
    </html>
    """
    return render_template_string(html_ui)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
