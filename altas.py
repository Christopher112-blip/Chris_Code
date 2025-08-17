""" AI DESKTOP ASSISTANT — CONVERSATIONAL (OPENAI) + VOICE + AUTOMATION

ARCHITECTURE DIAGRAM (text)

+--------------------+         +-----------------------+        +-------------------+ |  Voice Layer       |  audio  |  STT (Vosk/Whisper)  | text   | Conversation Core | | (hotkey / wake)    +-------->+  or Text Input       +------->+ (OpenAI NLU)      | +---------+----------+         +-----------+-----------+        +----+--------------+ ^                                    |                        | hotkey |                                    | intents/actions        | structured Ctrl+Alt+A                                   v                        v +---------+----------+         +-----------------------+        +-------------------+ |  TTS (pyttsx3)     |<--------+  Response Generator   |<-------+ Action Planner   | +--------------------+   text  +-----------------------+        +---------+---------+ | v +-----------------  ACTION EXECUTOR  ------------------+ |  Apps/OS (open, windows, hotkeys, multi-desktop)    | |  Web automation (Playwright)                         | |  Clipboard & Files (pyperclip/shutil)                | |  Reminders/Alarms + Folder Watch (SQLite/watchdog)  | |  Monitoring (psutil)                                 | +---------------------+--------------------------------+ | v +----------+-----------+ | Memory & Knowledge   | |  - SQLite (facts,    | |    reminders, logs)  | |  - Graph (networkx)  | +----------+-----------+ | v +--------+---------+ |  Cross-Device    | |  (optional Flask | |   API / FCM)     | +------------------+

NOTES:

All optional deps are sandboxed with try/except so the app runs even if some features are not installed/configured.

Set environment variables before running: OPENAI_API_KEY=...   (for conversational NLU) ASSISTANT_WAKE_WORD (optional, default "hey atlas")


"""

import os import sys import re import json import time import threading import sqlite3 import queue import platform import subprocess import logging import shutil from datetime import datetime, timedelta from urllib.parse import quote_plus

---------- Optional dependencies (soft)

try: import requests except Exception: requests = None

try: from plyer import notification except Exception: notification = None

try: import pyautogui except Exception: pyautogui = None

try: import pyperclip except Exception: pyperclip = None

try: from watchdog.observers import Observer from watchdog.events import FileSystemEventHandler except Exception: Observer = None FileSystemEventHandler = object

try: from playwright.sync_api import sync_playwright except Exception: sync_playwright = None

try: import psutil except Exception: psutil = None

try: import networkx as nx except Exception: nx = None

try: import keyboard  # global hotkeys except Exception: keyboard = None

try: import pyttsx3  # TTS except Exception: pyttsx3 = None

Voice/STT (choose one; both optional)

try: import vosk, sounddevice as sd, queue as sdqueue except Exception: vosk = None sd = None sdqueue = None

OpenAI (conversational NLU)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") try: from openai import OpenAI _openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None except Exception: _openai_client = None

---------- Globals & setup

APP_NAME = "Atlas Assistant" DB_PATH = os.path.join(os.path.dirname(file), "assistant.db") STATE = { "stop": False, "playwright": None, "browser": None, "page": None, "watchers": {}, "tts": None, } TASK_Q = queue.Queue() logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

---------- Utilities

def notify(title, message): if notification: try: notification.notify(title=title, message=message, timeout=6) except Exception: pass print(f" [NOTIFY] {title}: {message}")

def tts_say(text: str): if not pyttsx3: return if not STATE["tts"]: try: STATE["tts"] = pyttsx3.init() except Exception: return try: STATE["tts"].say(text) STATE["tts"].runAndWait() except Exception: pass

---------- Persistence (SQLite)

def db_init(): con = sqlite3.connect(DB_PATH) cur = con.cursor() cur.execute("CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, text TEXT, due_at TEXT, repeat_rule TEXT)") cur.execute("CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, payload TEXT)") cur.execute("CREATE TABLE IF NOT EXISTS facts(key TEXT PRIMARY KEY, value TEXT)") cur.execute("CREATE TABLE IF NOT EXISTS habits(id INTEGER PRIMARY KEY, ts TEXT, action TEXT)") con.commit(); con.close()

def db_log(kind, payload): con = sqlite3.connect(DB_PATH) cur = con.cursor() cur.execute("INSERT INTO logs(ts, kind, payload) VALUES(?,?,?)", (datetime.now().isoformat(timespec='seconds'), kind, json.dumps(payload))) con.commit(); con.close()

---------- Reminders & Alarms

def add_reminder(text, due_at, repeat_rule=None): con = sqlite3.connect(DB_PATH) cur = con.cursor() cur.execute("INSERT INTO reminders(text, due_at, repeat_rule) VALUES(?,?,?)", (text, due_at, repeat_rule)) con.commit(); con.close()

def list_reminders(): con = sqlite3.connect(DB_PATH) cur = con.cursor() cur.execute("SELECT id, text, due_at, repeat_rule FROM reminders ORDER BY due_at ASC") rows = cur.fetchall(); con.close(); return rows

def delete_reminder(rid): con = sqlite3.connect(DB_PATH) cur = con.cursor() cur.execute("DELETE FROM reminders WHERE id=?", (rid,)) con.commit(); con.close()

def scheduler_loop(): while not STATE["stop"]: try: now = datetime.now() con = sqlite3.connect(DB_PATH) cur = con.cursor() cur.execute("SELECT id, text, due_at, repeat_rule FROM reminders") rows = cur.fetchall(); con.close() for rid, text, due_at, repeat_rule in rows: try: dt = datetime.fromisoformat(due_at) except Exception: continue if now >= dt: notify("Reminder", text) tts_say(f"Reminder: {text}") if repeat_rule: if repeat_rule == "daily": nxt = dt + timedelta(days=1) elif repeat_rule == "hourly": nxt = dt + timedelta(hours=1) elif repeat_rule == "weekly": nxt = dt + timedelta(weeks=1) else: nxt = None if nxt: con = sqlite3.connect(DB_PATH) cur = con.cursor() cur.execute("UPDATE reminders SET due_at=? WHERE id=?", (nxt.isoformat(timespec='minutes'), rid)) con.commit(); con.close(); continue delete_reminder(rid) except Exception: pass time.sleep(10)

---------- Clipboard & Files

def set_clipboard(text): if not pyperclip: return "pyperclip not installed" try: pyperclip.copy(text); return "ok" except Exception as e: return f"failed: {e}"

def get_clipboard(): if not pyperclip: return None try: return pyperclip.paste() except Exception: return None

def move_file(src, dst): try: os.makedirs(os.path.dirname(dst), exist_ok=True) shutil.move(src, dst); return "ok" except Exception as e: return f"failed: {e}"

def delete_path(p): try: if os.path.isdir(p): shutil.rmtree(p) elif os.path.isfile(p): os.remove(p) else: return "not found" return "ok" except Exception as e: return f"failed: {e}"

---------- Monitoring (psutil)

def get_system_stats(): if not psutil: return {"error":"psutil not installed"} try: return { "cpu_percent": psutil.cpu_percent(interval=0.5), "mem_percent": psutil.virtual_memory().percent, "disk_percent": psutil.disk_usage("/").percent if platform.system()!="Windows" else psutil.disk_usage("C:/").percent, "battery_percent": (psutil.sensors_battery().percent if hasattr(psutil, 'sensors_battery') and psutil.sensors_battery() else None), } except Exception as e: return {"error": str(e)}

---------- Multi-desktop / Windowing (best-effort cross-platform)

def switch_desktop(idx: int): try: if platform.system()=="Linux": # requires wmctrl subprocess.Popen(["wmctrl", "-s", str(idx)]) return "ok" elif platform.system()=="Darwin": script = f'tell application "System Events" to key code 18 using control down'  # Ctrl+1 as example subprocess.run(["osascript", "-e", script]) return "ok" elif platform.system()=="Windows": # No stdlib way; recommend third-party libs. We'll provide a message. return "windows multi-desktop control requires additional tools" except Exception as e: return f"failed: {e}"

def send_hotkeys(*keys): if not pyautogui: return "pyautogui not installed" try: pyautogui.hotkey(*keys); return "ok" except Exception as e: return f"failed: {e}"

---------- Web automation (Playwright)

def ensure_browser(): if not sync_playwright: return "playwright not installed" if not STATE["playwright"]: STATE["playwright"] = sync_playwright().start() STATE["browser"] = STATE["playwright"].chromium.launch(headless=False) STATE["page"] = STATE["browser"].new_page() return None

def web_open(url): err = ensure_browser(); if err: return err STATE["page"].goto(url) return "ok"

def web_type(selector, text): if not STATE.get("page"): return "no page" STATE["page"].fill(selector, text); return "ok"

def web_click(selector): if not STATE.get("page"): return "no page" STATE["page"].click(selector); return "ok"

---------- Maps/Time/Weather (no key)

def get_public_ip_location(): if not requests: return (None, None, None) try: r = requests.get("https://ipinfo.io/json", timeout=5) j = r.json(); if "loc" in j: lat, lon = j["loc"].split(","); return float(lat), float(lon), j.get("city") except Exception: pass return (None, None, None)

def geocode_city(city): if not requests: return (None, None, None) try: r = requests.get("https://nominatim.openstreetmap.org/search", params={"q": city, "format":"json", "limit": 1}, headers={"User-Agent":"atlas-assistant"}, timeout=10) arr = r.json() if arr: return float(arr[0]["lat"]), float(arr[0]["lon"]), arr[0].get("display_name") except Exception: pass return (None, None, None)

def weather_by_coords(lat, lon): if not requests: return None try: url = "https://api.open-meteo.com/v1/forecast" params = {"latitude": lat, "longitude": lon, "current_weather": True} j = requests.get(url, params=params, timeout=10).json() cw = j.get("current_weather") if cw: return {"temperature_c": cw.get("temperature"), "wind_kmh": cw.get("windspeed"), "code": cw.get("weathercode")} except Exception: pass return None

def open_url(url): try: if platform.system()=="Windows": os.startfile(url) elif platform.system()=="Darwin": subprocess.Popen(["open", url]) else: subprocess.Popen(["xdg-open", url]) except Exception: pass

---------- Knowledge Graph (optional networkx)

class Knowledge: def init(self, path): self.path = path self.g = nx.Graph() if nx else None self._load() def _load(self): if not nx: return if os.path.isfile(self.path): try: data = json.load(open(self.path, "r", encoding="utf-8")) self.g = nx.node_link_graph(data) except Exception: self.g = nx.Graph() def _save(self): if not nx: return data = nx.node_link_data(self.g) json.dump(data, open(self.path, "w", encoding="utf-8")) def add_fact(self, subject, predicate, obj): if not nx: return "networkx not installed" self.g.add_node(subject); self.g.add_node(obj) self.g.add_edge(subject, obj, predicate=predicate, ts=datetime.now().isoformat(timespec='seconds')) self._save(); return "ok" def query(self, subject): if not nx: return [] if subject not in self.g: return [] res = [] for n in self.g.neighbors(subject): pred = self.g.get_edge_data(subject, n).get("predicate") res.append({"subject": subject, "predicate": pred, "object": n}) return res

KNOWLEDGE = Knowledge(os.path.join(os.path.dirname(file), "knowledge.json"))

---------- Habits (very simple model)

def record_habit(action): con = sqlite3.connect(DB_PATH) cur = con.cursor() cur.execute("INSERT INTO habits(ts, action) VALUES(?, ?)", (datetime.now().isoformat(timespec='seconds'), action)) con.commit(); con.close()

def suggest_actions_now(limit=3): con = sqlite3.connect(DB_PATH) cur = con.cursor() hour = datetime.now().hour cur.execute("SELECT action, COUNT(*) c FROM habits WHERE CAST(substr(ts,12,2) AS INTEGER)=? GROUP BY action ORDER BY c DESC LIMIT ?", (hour, limit)) rows = cur.fetchall(); con.close() return [a for a,_ in rows]

---------- Internet power (stubs with graceful fallback)

def web_search(query): if not requests: open_url(f"https://duckduckgo.com/?q={quote_plus(query)}"); return ["opened browser for search"] try: # Use duckduckgo html as a simple fallback (not guaranteed stable) url = "https://duckduckgo.com/html/" r = requests.post(url, data={"q": query}, timeout=10, headers={"User-Agent":"atlas-assistant"}) if r.status_code==200: return [f"Top results page opened in browser"], open_url(f"https://duckduckgo.com/?q={quote_plus(query)}") except Exception: pass open_url(f"https://duckduckgo.com/?q={quote_plus(query)}"); return ["opened browser for search"]

def gmail_send_email(*args, **kwargs): return "gmail not configured; integrate Google API creds first"

def calendar_create_event(*args, **kwargs): return "calendar not configured; integrate Google Calendar API first"

def fetch_rss(url): if not requests: return [] try: import xml.etree.ElementTree as ET txt = requests.get(url, timeout=10).text root = ET.fromstring(txt) items = [] for item in root.iter('item'): title = item.findtext('title') link = item.findtext('link') items.append({"title": title, "link": link}) return items[:10] except Exception: return []

def translate_text(text, target_lang="en"): # no external key; use web browser fallback open_url(f"https://translate.google.com/?sl=auto&tl={quote_plus(target_lang)}&text={quote_plus(text)}"); return "opened translator"

def dictionary_lookup(word): open_url(f"https://www.lexico.com/en/definition/{quote_plus(word)}"); return "opened dictionary"

---------- Command registry (actions the planner can call)

def action_open_app(name): try: if platform.system()=="Windows": os.startfile(name) else: subprocess.Popen([name]) record_habit(f"open_app:{name}") return f"Opened {name}" except Exception as e: return f"Failed to open app: {e}"

def action_open_url(url): open_url(url); record_habit("open_url"); return f"Opened {url}"

def action_weather(city=None): if city: lat, lon, disp = geocode_city(city) else: lat, lon, disp = get_public_ip_location() if not lat: return "Couldn't resolve location" data = weather_by_coords(lat, lon) if not data: return "Weather unavailable" return f"Weather @ {disp or f'{lat:.3f},{lon:.3f}'}: {data['temperature_c']}°C, wind {data['wind_kmh']} km/h"

def action_set_reminder(text, due_iso, repeat=None): add_reminder(text, due_iso, repeat) return f"Reminder set for {due_iso}"

def action_system_stats(): s = get_system_stats() if "error" in s: return s["error"] return f"CPU {s['cpu_percent']}%, MEM {s['mem_percent']}%, DISK {s['disk_percent']}%, BAT {s['battery_percent']}%"

def action_clipboard_set(text): return set_clipboard(text)

def action_clipboard_get(): val = get_clipboard(); return val or "empty"

def action_file_move(src, dst): return move_file(src, dst)

def action_file_delete(path): return delete_path(path)

def action_search(query): web_search(query); return f"Searching for {query}"

def action_switch_desktop(idx): return switch_desktop(idx)

def action_hotkeys(keys_csv): keys = [k.strip() for k in keys_csv.split(',')] return send_hotkeys(*keys)

ACTIONS = { "open_app": action_open_app, "open_url": action_open_url, "weather": action_weather, "set_reminder": action_set_reminder, "system_stats": action_system_stats, "clipboard_set": action_clipboard_set, "clipboard_get": action_clipboard_get, "file_move": action_file_move, "file_delete": action_file_delete, "search": action_search, "switch_desktop": action_switch_desktop, "hotkeys": action_hotkeys, }

---------- Natural Language Understanding (OpenAI planner)

SYSTEM_PROMPT = ( "You are an assistant that converts user requests into a short response plus a JSON action plan. " "If an action is needed, respond with a JSON object under 'action' containing 'name' and 'args'. " "Valid actions: " + ", ".join(ACTIONS.keys()) + ". " "If setting a reminder, compute an ISO datetime if the user says times like 'in 30 minutes'. " )

def plan_with_openai(user_text: str): if not _openai_client: return {"reply": "(OpenAI not configured) " + user_text, "action": None} try: messages = [ {"role":"system","content": SYSTEM_PROMPT}, {"role":"user","content": user_text}, ] resp = _openai_client.chat.completions.create( model="gpt-4o-mini", messages=messages, temperature=0.2, response_format={"type":"json_object"} ) j = json.loads(resp.choices[0].message.content) return {"reply": j.get("reply") or "", "action": j.get("action")} except Exception as e: return {"reply": f"NLU error: {e}", "action": None}

---------- Voice: hotkey to start listening (vosk)

class VoskListener(threading.Thread): def init(self, wake_word="hey atlas"): super().init(daemon=True) self.wake = wake_word.lower() self.active = False def run(self): if not vosk or not sd: logging.info("Vosk not available; voice disabled") return try: model = vosk.Model(lang="en-us") q = sdqueue.Queue() def cb(indata, frames, time_, status): q.put(bytes(indata)) with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype='int16', channels=1, callback=cb): rec = vosk.KaldiRecognizer(model, 16000) logging.info("Voice listener ready") while not STATE["stop"]: data = q.get() if rec.AcceptWaveform(data): text = json.loads(rec.Result()).get("text", "").lower() if not text: continue if not self.active: if self.wake in text: notify(APP_NAME, "Listening...") self.active = True else: handle_user_text(text) self.active = False else: pass except Exception as e: logging.warning(f"Vosk error: {e}")

---------- Dispatcher

def handle_user_text(user_text: str): db_log("utterance", {"text": user_text}) plan = plan_with_openai(user_text) reply = plan.get("reply") or "" action = plan.get("action") if reply: print(f" ASSISTANT: {reply}") tts_say(reply) if action and isinstance(action, dict): name = action.get("name"); args = action.get("args", {}) fn = ACTIONS.get(name) if fn: try: res = fn(**args) if isinstance(args, dict) else fn(*args) print(f"[action:{name}] {res}") db_log("action", {"name": name, "args": args, "result": str(res)}) except Exception as e: print(f"[action:{name}] error: {e}") else: print(f"unknown action: {name}")

---------- Hotkey (Ctrl+Alt+A) to prompt

def register_hotkey(): if not keyboard: logging.info("keyboard module not installed; hotkey disabled") return def on_hotkey(): try: print(" [Hotkey] Type your command:") user_text = input("> ") handle_user_text(user_text) except Exception: pass keyboard.add_hotkey("ctrl+alt+a", on_hotkey) logging.info("Hotkey registered: Ctrl+Alt+A")

---------- CLI fallback loop

def cli_loop(): print(" Type to chat. Press Ctrl+C to quit. Use hotkey Ctrl+Alt+A anytime.") while not STATE["stop"]: try: user_text = input("you> ").strip() if not user_text: continue if user_text.lower() in ("exit","quit"): break handle_user_text(user_text) except (EOFError, KeyboardInterrupt): break

---------- Main

def main(): db_init() t_sched = threading.Thread(target=scheduler_loop, daemon=True); t_sched.start()

# Start voice listener (wake-word) if available
VoskListener(os.getenv("ASSISTANT_WAKE_WORD", "hey atlas")).start()

# Register global hotkey
register_hotkey()

# Suggest habits
sug = suggest_actions_now()
if sug:
    print(f"Suggestions for this hour: {', '.join(sug)}")

print(f"{APP_NAME} ready. OpenAI={'on' if _openai_client else 'off'}. Voice={'on' if vosk and sd else 'off'}. Hotkey Ctrl+Alt+A.")
cli_loop()

STATE["stop"] = True
if STATE["browser"]:
    try: STATE["browser"].close()
    except Exception: pass
if STATE["playwright"]:
    try: STATE["playwright"].stop()
    except Exception: pass

if name == "main": main()

