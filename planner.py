import os
import json
import sqlite3
import requests
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
DB_FILE = "mealsync.db"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

SYSTEM_INSTRUCTION = """
You are the MealSync Intelligence Engine, an expert Maharashtrian & Indian pure-vegetarian kitchen planner.
Your responsibilities:
1. Balance weekly variety (rotate sprouts, lentils/dals, leafy greens, dry sabzis, light vs. heavy dinners).
2. Calculate time-shifted advance prep tasks (e.g., if Sabudana Khichdi is scheduled for tomorrow's breakfast, trigger soaking in today's evening tasks; if Matki Usal is planned, trigger sprouting/soaking 24h prior).
3. Identify low-stock pantry staples and generate proactive refill suggestions.
4. Output strictly valid JSON matching the exact MealSync schema. All dish names and daily tasks must be concise and rendered naturally in Marathi.
"""

FEW_SHOT_EXAMPLES = [
    {
        "input": {
            "target_day": "Tuesday",
            "requested_theme": "Fasting / Light morning",
            "previous_day_menu": { "dinner": "वरण भात, भाजी" },
            "pantry_state": { "sabudana": "low", "peanuts": "available" }
        },
        "output": {
            "breakfast": "साबुदाणा खिचडी, शेंगदाणा कूट",
            "lunch": "उपवासाची बटाटा भाजी, राजगिरा पुरी",
            "dinner": "वरई भात, शेंगदाणा आमटी",
            "task1": "शाबुदाणा रात्री भिजत घालणे",
            "task2": "शेंगदाणे भाजून कूट करणे",
            "prep_alert": "उद्याच्या उसळीसाठी मटकी भिजवणे",
            "pantry_shopping_list": ["साबुदाणा (Sabudana) - 1 kg"]
        }
    },
    {
        "input": {
            "target_day": "Wednesday",
            "requested_theme": "Sprout-rich lunch & fermented breakfast",
            "previous_day_menu": { "dinner": "वरई भात, शेंगदाणा आमटी" },
            "pantry_state": { "idli_rava": "available", "urad_dal": "low", "matki": "available" }
        },
        "output": {
            "breakfast": "इडली, ओल्या खोबऱ्याची चटणी",
            "lunch": "मोड आलेल्या मटकीची उसळ, पोळी, वरण",
            "dinner": "मुगाची मऊ खिचडी, कढी",
            "task1": "इडलीचे पीठ आंबवणे",
            "task2": "मटकीला मोड आणणे",
            "prep_alert": "उद्यासाठी मेथी निवडून ठेवणे",
            "pantry_shopping_list": ["उडीद डाळ (Urad Dal) - 1 kg"]
        }
    }
]

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def generate_day_plan(target_day, user_theme="Balanced vegetarian home meal", pantry_overrides=None):
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is not configured.")

    if pantry_overrides is None:
        pantry_overrides = {"matki": "available", "poha": "available", "dal": "available"}

    # Fetch context from other days to avoid repetitive suggestions
    with get_db() as conn:
        rows = conn.execute("SELECT day_name, lunch, dinner FROM weekly_menu WHERE day_name != ?", (target_day,)).fetchall()
        week_context = "; ".join([f"{r['day_name']}: Lunch={r['lunch']}, Dinner={r['dinner']}" for r in rows])

    prompt_data = {
        "target_day": target_day,
        "requested_theme": user_theme,
        "weekly_context_to_avoid_repetition": week_context,
        "pantry_state": pantry_overrides
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_INSTRUCTION}]
        },
        "contents": [
            {"role": "user", "parts": [{"text": f"Input:\n{json.dumps(FEW_SHOT_EXAMPLES[0]['input'])}"}]},
            {"role": "model", "parts": [{"text": json.dumps(FEW_SHOT_EXAMPLES[0]['output'])}]},
            {"role": "user", "parts": [{"text": f"Input:\n{json.dumps(FEW_SHOT_EXAMPLES[1]['input'])}"}]},
            {"role": "model", "parts": [{"text": json.dumps(FEW_SHOT_EXAMPLES[1]['output'])}]},
            {"role": "user", "parts": [{"text": f"Input:\n{json.dumps(prompt_data)}"}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.4
        }
    }

    response = requests.post(url, json=payload, timeout=15)
    if response.status_code == 200:
        raw_text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        plan = json.loads(raw_text)
        
        # Save generated menu directly into SQLite
        with get_db() as conn:
            conn.execute("""
                UPDATE weekly_menu
                SET breakfast = ?, lunch = ?, dinner = ?, task1 = ?, task2 = ?
                WHERE day_name = ?
            """, (plan["breakfast"], plan["lunch"], plan["dinner"], plan["task1"], plan["task2"], target_day))
            conn.commit()
            
        return plan
    else:
        raise RuntimeError(f"Gemini API returned error code {response.status_code}: {response.text}")
