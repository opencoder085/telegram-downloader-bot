import sys
import gc
import math
import zoneinfo
import os
import re
import difflib
import json
import ssl
import shutil
import zipfile
import asyncio
import tempfile
import threading
import time
import struct
import urllib.parse
import urllib.request
import calendar
import html as html_lib
import resource
import collections
import traceback
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import httpx
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import docx
import openpyxl
import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import pytesseract
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    TypeHandler,
)
import yt_dlp

# =====================================================================
# ÖN-DERLENMİŞ DÜZENLİ İFADELER (PRE-COMPILED REGEX PATTERNS)
# =====================================================================
RE_CLEAN_TIME = re.compile(r"\d{1,2}:\d{2}")
RE_TIME_PARTS = re.compile(r"(\d{1,2})[:.](\d{2})")
RE_MINUTES = re.compile(r"(\d+)\s*(?:daqiqa|dakika|min|m|минут)", re.IGNORECASE)
RE_URL_HTTP = re.compile(r"https?://[^\s]+")
RE_TZ_SEARCH = re.compile(r"(?:(?:utc|gmt)\s*)?([+-]\d{1,2})", re.IGNORECASE)
RE_INSTA_CODE = re.compile(r'instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv|share/reel|share/p)/([A-Za-z0-9_-]+)')
RE_TWITTER_ID = re.compile(r'(?:twitter\.com|x\.com)/(?:[^/]+/)?status/(\d+)')
RE_CYRILLIC_LETTERS = re.compile(r'[\u0400-\u04FF]')
RE_LATIN_LETTERS = re.compile(r'[a-zA-Z]')
RE_NORMALIZE_CHARS = re.compile(r"['’`ʻʼ\u02bb\u02bc\-]")
RE_SUPPORTED_PLATFORMS = [
    re.compile(r'(?:instagram\.com|instagr\.am|threads\.net)', re.IGNORECASE),
    re.compile(r'(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)', re.IGNORECASE),
    re.compile(r'(?:facebook\.com|fb\.watch|fb\.gg|fb\.me|m\.facebook\.com)', re.IGNORECASE),
    re.compile(r'(?:twitter\.com|x\.com)', re.IGNORECASE),
    re.compile(r'(?:youtube\.com|youtu\.be)', re.IGNORECASE)
]

# =====================================================================
# SUNUCU METRİKLERİ & BAŞLANGIÇ ZAMANI
# =====================================================================
BOT_START_TIME = time.time()

# =====================================================================
# SİSTEM GÜNLÜĞÜ (RING BUFFER LOGGING)
# =====================================================================
SYSTEM_LOGS = collections.deque(maxlen=40)

def add_system_log(msg: str, level: str = "INFO"):
    now_str = datetime.now().strftime("%H:%M:%S")
    entry = f"[{now_str}] [{level}] {msg}"
    SYSTEM_LOGS.append(entry)
    print(f"[NUN_LOG] {entry}")

add_system_log("Nun Bot altyapısı ve iş parçacıkları başlatıldı.", level="INIT")

# =====================================================================
# GLOBAL VE KALICI HTTP BAĞLANTI HAVUZU (KEEP-ALIVE POOL)
# =====================================================================
GLOBAL_HTTP_CLIENT = None

def get_http_client() -> httpx.AsyncClient:
    global GLOBAL_HTTP_CLIENT
    if GLOBAL_HTTP_CLIENT is None or GLOBAL_HTTP_CLIENT.is_closed:
        limits = httpx.Limits(max_keepalive_connections=25, max_connections=60, keepalive_expiry=30.0)
        GLOBAL_HTTP_CLIENT = httpx.AsyncClient(timeout=12.0, follow_redirects=True, limits=limits)
    return GLOBAL_HTTP_CLIENT

def safe_md(text: str) -> str:
    if not text:
        return ""
    return str(text).replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")

# =====================================================================
# GLOBAL VE KALICI SENKRON HTTP İSTEMCİSİ (KEEP-ALIVE POOL)
# =====================================================================
GLOBAL_SYNC_HTTP_CLIENT = None

def get_sync_http_client() -> httpx.Client:
    global GLOBAL_SYNC_HTTP_CLIENT
    if GLOBAL_SYNC_HTTP_CLIENT is None or GLOBAL_SYNC_HTTP_CLIENT.is_closed:
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=60.0)
        GLOBAL_SYNC_HTTP_CLIENT = httpx.Client(timeout=25.0, follow_redirects=True, limits=limits)
    return GLOBAL_SYNC_HTTP_CLIENT

# =====================================================================
# GÜVENLİK: RATE LIMITING & FLOOD KORUMASI (ANTI-SPAM DEBOUNCE)
# =====================================================================
RATE_LIMIT_STORE = {}

def is_rate_limited(user_id: int, window: float = 0.5) -> bool:
    if not user_id:
        return False
    now = time.time()
    last = RATE_LIMIT_STORE.get(user_id, 0.0)
    if now - last < window:
        return True
    RATE_LIMIT_STORE[user_id] = now
    if len(RATE_LIMIT_STORE) > 5000:
        cutoff = now - 30.0
        for k in [k for k, v in RATE_LIMIT_STORE.items() if v < cutoff]:
            RATE_LIMIT_STORE.pop(k, None)
    return False

# =====================================================================
# RENDER 7/24 SAĞLIK SUNUCUSU & SELF-PINGER
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ('/ping', '/health', '/'):
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"status": "ok", "service": "NUN_BOT_24_7"}')
            return

        self.send_response(404)
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

def get_target_ping_url() -> str:
    hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if hostname:
        return f"https://{hostname}/ping"
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL") or os.environ.get("WEB_URL")
    if url:
        if not url.startswith("http"):
            url = f"https://{url}"
        return f"{url.rstrip('/')}/ping"
    return None

def run_keep_alive_pinger():
    time.sleep(60)
    while True:
        target_url = get_target_ping_url()
        if target_url:
            try:
                req = urllib.request.Request(
                    target_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) NunBot-KeepAlive/3.0'}
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    print(f"[KEEP-ALIVE] Ping basarili: {target_url} -> Kod: {resp.status}")
            except Exception as e:
                print(f"[KEEP-ALIVE] Ping uyarisi: {e}")
        time.sleep(480)

# =====================================================================
# SAHİP VE TELEMETRİ YÖNETİMİ
# =====================================================================
ADMIN_IDS = {1885043735, 8576061834, 2146753102}
ADMINS_FILE = "admin_ids.json"
USER_PROFILES_FILE = "user_profiles.json"
USER_PROFILES = {}

def load_admin_ids():
    global ADMIN_IDS
    env_admins = os.environ.get("ADMIN_ID") or os.environ.get("OWNER_ID") or os.environ.get("ADMINS", "")
    for p in re.split(r"[,;\s]+", env_admins):
        if p.strip().isdigit():
            ADMIN_IDS.add(int(p.strip()))
    if os.path.exists(ADMINS_FILE):
        try:
            with open(ADMINS_FILE, "r", encoding="utf-8") as f:
                ADMIN_IDS.update(json.load(f))
        except Exception:
            pass

def save_admin_ids():
    try:
        with open(ADMINS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(ADMIN_IDS), f)
    except Exception:
        pass

LAST_PROFILE_SAVE_TS = 0.0

def add_admin_id(uid: int) -> bool:
    global ADMIN_IDS
    ADMIN_IDS.add(uid)
    save_admin_ids()
    invalidate_users_cache()
    return True

def remove_admin_id(uid: int) -> tuple:
    global ADMIN_IDS
    if len(ADMIN_IDS) <= 1:
        return False, "Son kalan yönetici silinemez."
    if uid in ADMIN_IDS:
        ADMIN_IDS.remove(uid)
        save_admin_ids()
        invalidate_users_cache()
        return True, "Yönetici başarıyla çıkarıldı."
    return False, "Bu ID yönetici listesinde bulunamadı."

async def global_user_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and not user.is_bot:
        track_user_activity(user)

def track_user_activity(user):
    global LAST_PROFILE_SAVE_TS
    if not user:
        return
    uid = user.id
    uid_str = str(uid)
    name = (f"{user.first_name or ''} {user.last_name or ''}").strip() or f"User {uid}"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    is_new = uid_str not in USER_PROFILES
    p = USER_PROFILES.get(uid_str, {})
    p['id'] = uid
    if name:
        p['name'] = name
    elif 'name' not in p:
        p['name'] = f"User {uid}"

    if getattr(user, 'username', None):
        p['username'] = user.username.lstrip('@')
    elif 'username' not in p:
        p['username'] = ""

    p['last_active'] = now_str
    USER_PROFILES[uid_str] = p

    if uid_str not in USER_LANGS:
        tele_lang = getattr(user, 'language_code', None) or 'uz'
        init_lang = 'tr' if tele_lang.startswith('tr') else ('ru' if tele_lang.startswith('ru') else ('en' if tele_lang.startswith('en') else 'uz'))
        USER_LANGS[uid_str] = init_lang
        save_json(LANG_FILE, USER_LANGS)

    p['bot_blocked'] = False
    if is_new:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(notify_new_user_to_admins(user))
        except RuntimeError:
            pass

    now_t = time.time()
    if is_new or (now_t - LAST_PROFILE_SAVE_TS > 15.0):
        LAST_PROFILE_SAVE_TS = now_t
        save_json(USER_PROFILES_FILE, USER_PROFILES)
        invalidate_users_cache()


def log_module_action(module_name: str, user_id: int = None):
    if module_name in MODULE_STATS:
        MODULE_STATS[module_name] += 1
    else:
        MODULE_STATS[module_name] = 1
    save_json(MODULE_STATS_FILE, MODULE_STATS)
    if user_id:
        uid_str = str(user_id)
        if uid_str in USER_PROFILES:
            USER_PROFILES[uid_str]['last_action'] = module_name
            save_json(USER_PROFILES_FILE, USER_PROFILES)

def mark_user_blocked(user_id: int):
    uid_str = str(user_id)
    if uid_str in USER_PROFILES:
        USER_PROFILES[uid_str]['bot_blocked'] = True
        save_json(USER_PROFILES_FILE, USER_PROFILES)
        invalidate_users_cache()

def calculate_activity_metrics():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    dau, wau, passive = 0, 0, 0
    for p in USER_PROFILES.values():
        la_str = p.get('last_active', '')
        if not la_str or "Kayıtlı" in la_str:
            passive += 1
            continue
        try:
            la_dt = datetime.strptime(la_str[:19], "%Y-%m-%d %H:%M:%S")
            diff = now - la_dt
            if diff <= timedelta(days=1):
                dau += 1
            if diff <= timedelta(days=7):
                wau += 1
            if diff > timedelta(days=30):
                passive += 1
        except Exception:
            passive += 1
    return dau, wau, passive

async def notify_new_user_to_admins(user):
    global GLOBAL_BOT
    if not GLOBAL_BOT:
        return
    uid = user.id
    raw_name = (f"{user.first_name or ''} {user.last_name or ''}").strip() or f"User {uid}"
    name_clean = html_lib.escape(raw_name)
    uname = user.username
    uname_str = f"@{html_lib.escape(uname)}" if uname else "<i>(Username yok)</i>"
    tele_lang = (user.language_code or "uz").upper()
    total_users = len(get_all_registered_users())
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    card = (
        "🎉 <b>NUN PROJECT // YENİ KULLANICI KATILDI!</b>\n\n"
        f"👤 <b>İsim:</b> {name_clean}\n"
        f"🔗 <b>Kullanıcı Adı:</b> {uname_str}\n"
        f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n"
        f"🌐 <b>Cihaz Dili:</b> <code>{tele_lang}</code>\n"
        f"⏰ <b>Kayıt Zamanı:</b> <code>{now_str}</code>\n\n"
        f"📊 <b>Toplam Kayıtlı Kullanıcı:</b> <code>{total_users}</code>"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Kullanıcıyı Aç", callback_data=f"admin_user_view_{uid}"),
            InlineKeyboardButton("✉️ DM Gönder", callback_data=f"admin_dm_start_{uid}")
        ]
    ])
    for aid in ADMIN_IDS:
        try:
            await GLOBAL_BOT.send_message(chat_id=aid, text=card, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass

async def notify_security_flood_alert(user_id: int, req_count: int):
    global GLOBAL_BOT
    if not GLOBAL_BOT:
        return
    p = USER_PROFILES.get(str(user_id), {})
    nm = html_lib.escape(p.get('name') or f"User {user_id}")
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    alert_card = (
        "🚨 <b>NUN PROJECT // GÜVENLİK ALARMI (FLOODSHIELD)</b>\n\n"
        "⚠️ <b>Şüpheli İstek Saldırısı Tespit Edildi!</b>\n"
        f"👤 <b>Kullanıcı:</b> {nm} (<code>{user_id}</code>)\n"
        f"⚡ <b>Hız:</b> 10 saniyede <code>{req_count}</code> istek\n"
        "🛡️ <b>Otomatik Tedbir:</b> 10 dakika boyunca kısıtlandı (Jail)\n"
        f"⏰ <b>Zaman:</b> <code>{now_str}</code>"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Kullanıcıyı İncele", callback_data=f"admin_user_view_{user_id}"),
            InlineKeyboardButton("⛔ Kalıcı Banla", callback_data=f"admin_ban_hard_quick_{user_id}")
        ]
    ])
    for aid in ADMIN_IDS:
        try:
            await GLOBAL_BOT.send_message(chat_id=aid, text=alert_card, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass

def format_module_stats_card() -> tuple:
    total = sum(MODULE_STATS.values())
    lines = [
        "🏆 <b>NUN PROJECT // MODÜL KULLANIM İSTATİSTİKLERİ</b>\n",
        f"Toplam Gerçekleştirilen İşlem: <code>{total}</code>\n"
    ]
    labels = {
        "prayer": ("🕌", "Namaz & İbadet"),
        "video": ("🎬", "Video & MP3 İndirme"),
        "exam_todo": ("🎓", "Sınav & To-Do"),
        "weather": ("🌤️", "Hava Durumu"),
        "pdf_hub": ("📄", "PDF & OCR Araçları"),
        "pomodoro": ("⏱️", "Pomodoro & Hatırlatıcı"),
        "adhkar": ("📿", "Zikirler & Salavat"),
        "translit": ("🔤", "Kiril ⇄ Latin Çeviri"),
        "hadith": ("📖", "Günün Hadisi")
    }
    sorted_items = sorted(MODULE_STATS.items(), key=lambda x: x[1], reverse=True)
    for idx, (mod, count) in enumerate(sorted_items, start=1):
        icon, name = labels.get(mod, ("▫️", mod.title()))
        pct = (count / total * 100) if total > 0 else 0
        lines.append(f"<b>{idx}.</b> {icon} <b>{name}:</b> <code>{count}</code> kez (<code>%{pct:.1f}</code>)")

    lines.append("\n💡 <i>Veriler gerçek zamanlı kaydedilmektedir.</i>")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kullanıcı Merkezine Dön", callback_data="admin_hub_users")]])
    return "\n".join(lines), kb

def get_ram_usage_mb() -> float:
    try:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        try:
            with open('/proc/self/status', 'r') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        return float(line.split()[1]) / 1024.0
        except Exception:
            return 0.0

def get_uptime_string() -> str:
    uptime_sec = int(time.time() - BOT_START_TIME)
    days = uptime_sec // 86400
    hours = (uptime_sec % 86400) // 3600
    mins = (uptime_sec % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days} gün")
    if hours > 0:
        parts.append(f"{hours} saat")
    parts.append(f"{mins} dk")
    return ", ".join(parts)

# =====================================================================
# FLOODSHIELD (AKILLI ANTİ-SPAM VE SALDIRI KORUMASI)
# =====================================================================
FLOOD_JAIL = {}
FLOOD_TRACKER = {}

def check_flood_shield(user_id: int) -> bool:
    if not user_id or user_id in ADMIN_IDS:
        return False
    now = time.time()
    jail_until = FLOOD_JAIL.get(user_id, 0.0)
    if now < jail_until:
        return True
    elif user_id in FLOOD_JAIL:
        FLOOD_JAIL.pop(user_id, None)

    if user_id not in FLOOD_TRACKER:
        FLOOD_TRACKER[user_id] = collections.deque(maxlen=20)
    dq = FLOOD_TRACKER[user_id]
    dq.append(now)

    if len(dq) >= 15 and (now - dq[0] < 10.0):
        FLOOD_JAIL[user_id] = now + 600.0
        add_system_log(f"FLOODSHIELD: {user_id} 10 saniyede {len(dq)} istek atti -> 10 dk kisitlandi!", level="SECURITY")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(notify_security_flood_alert(user_id, len(dq)))
        except RuntimeError:
            pass
        return True
    return False

# =====================================================================
# GELİŞMİŞ SAAT DİLİMİ VE KONUM MOTORU
# =====================================================================
CITY_TIMEZONE_MAP = {
    "toshkent": 5, "tashkent": 5, "samarqand": 5, "samarkand": 5, "buxoro": 5, "bukhara": 5,
    "andijon": 5, "andijan": 5, "namangan": 5, "fargona": 5, "fergana": 5, "qoqon": 5, "kokand": 5,
    "urganch": 5, "urgench": 5, "nukus": 5, "qarshi": 5, "karshi": 5, "navoiy": 5, "navoi": 5,
    "termiz": 5, "termez": 5, "guliston": 5, "gulistan": 5, "jizzax": 5, "jizzakh": 5,
    "xiva": 5, "khiva": 5, "margilon": 5, "margilan": 5, "angren": 5, "chirchiq": 5, "chirchik": 5,
    "olmaliq": 5, "almalyk": 5, "shahrisabz": 5, "denov": 5, "zarafshon": 5, "bekobod": 5,
    "rishton": 5, "rishtan": 5, "asaka": 5, "shahrixon": 5, "chust": 5, "quva": 5,

    "istanbul": 3, "ankara": 3, "izmir": 3, "bursa": 3, "antalya": 3, "konya": 3, "adana": 3,
    "gaziantep": 3, "sanliurfa": 3, "kocaeli": 3, "mersin": 3, "diyarbakir": 3, "hatay": 3,
    "manisa": 3, "kayseri": 3, "samsun": 3, "balikesir": 3, "kahramanmaras": 3, "van": 3,
    "aydin": 3, "denizli": 3, "sakarya": 3, "erzurum": 3, "mugla": 3, "eskisehir": 3, "trabzon": 3,
    "elazig": 3, "sivas": 3, "batman": 3, "rize": 3, "malatya": 3, "tekirdag": 3, "canakkale": 3,

    "moskva": 3, "moscow": 3, "piter": 3, "sankt-peterburg": 3, "spb": 3, "kazan": 3, "sochi": 3,
    "krasnodar": 3, "nizhny novgorod": 3, "samara": 4, "ufa": 5, "yekaterinburg": 5, "ekaterinburg": 5,
    "chelyabinsk": 5, "tyumen": 5, "omsk": 6, "novosibirsk": 7, "krasnoyarsk": 7, "irkutsk": 8,
    "yakutsk": 9, "vladivostok": 10,

    "almaty": 5, "astana": 5, "shymkent": 5, "bishkek": 6, "osh": 6, "dushanbe": 5,
    "ashgabat": 5, "baku": 4, "tbilisi": 4, "yerevan": 4,

    "dubai": 4, "dubay": 4, "abu dhabi": 4, "riyadh": 3, "makka": 3, "makkah": 3, "mecca": 3,
    "madina": 3, "medine": 3, "medina": 3, "doha": 3, "kuwait": 3, "muscat": 4, "tehran": 3,

    "london": 0, "dublin": 0, "lisbon": 0, "paris": 1, "parij": 1, "berlin": 1, "rome": 1, "rim": 1,
    "madrid": 1, "amsterdam": 1, "brussels": 1, "bryussel": 1, "vienna": 1, "vena": 1, "warsaw": 1,
    "varshava": 1, "prague": 1, "praqa": 1, "budapest": 1, "kyiv": 2, "kiev": 2, "athens": 2,
    "afina": 2, "bucharest": 2, "buxarest": 2, "helsinki": 2, "stockholm": 1, "oslo": 1,

    "seoul": 9, "seul": 9, "tokyo": 9, "beijing": 8, "pekin": 8, "shanghai": 8, "hong kong": 8,
    "singapore": 8, "singapur": 8, "kuala lumpur": 8, "bangkok": 7, "jakarta": 7, "delhi": 5, "mumbai": 5,

    "new york": -5, "ny": -5, "washington": -5, "boston": -5, "miami": -5, "toronto": -5,
    "chicago": -6, "dallas": -6, "houston": -6, "denver": -7, "los angeles": -8, "la": -8,
    "san francisco": -8, "seattle": -8, "vancouver": -8, "sydney": 10, "melbourne": 10
}

def resolve_tz_from_city(city_input: str):
    c_norm = re.sub(r"['’`ʻʼ]", "", city_input.lower().strip()).replace('i̇', 'i').replace('ı', 'i')
    if c_norm in CITY_TIMEZONE_MAP:
        return CITY_TIMEZONE_MAP[c_norm]
    matches = difflib.get_close_matches(c_norm, list(CITY_TIMEZONE_MAP.keys()), n=1, cutoff=0.75)
    if matches:
        return CITY_TIMEZONE_MAP[matches[0]]
    return None

def coords_to_tz_offset(lat: float, lon: float) -> int:
    if 37.0 <= lat <= 46.0 and 56.0 <= lon <= 73.5: return 5
    if 35.5 <= lat <= 42.5 and 25.5 <= lon <= 45.0: return 3
    if 50.0 <= lat <= 65.0 and 28.0 <= lon <= 55.0: return 3
    if 22.0 <= lat <= 27.5 and 51.5 <= lon <= 60.0: return 4
    if 16.0 <= lat <= 32.0 and 34.0 <= lon <= 51.5: return 3
    if 30.0 <= lat <= 46.0 and 124.0 <= lon <= 146.0: return 9
    if 1.0 <= lat <= 54.0 and 97.0 <= lon <= 124.0: return 8
    if 36.5 <= lat <= 43.5 and 67.0 <= lon <= 75.0: return 5
    if 39.0 <= lat <= 43.5 and 69.0 <= lon <= 80.5: return 6
    if 40.5 <= lat <= 55.5 and 46.5 <= lon <= 87.5: return 5
    if 49.0 <= lat <= 60.5 and -11.0 <= lon <= 2.0: return 0
    if 36.0 <= lat <= 55.0 and 2.0 <= lon <= 24.0: return 1
    if 34.0 <= lat <= 70.0 and 20.0 <= lon <= 35.0: return 2
    if 24.0 <= lat <= 50.0:
        if -80.0 <= lon <= -65.0: return -5
        if -90.0 <= lon <= -80.0: return -5
        if -105.0 <= lon <= -90.0: return -6
        if -115.0 <= lon <= -105.0: return -7
        if -125.0 <= lon <= -115.0: return -8
    return max(-12, min(14, round(lon / 15.0)))

def parse_tz_from_text(text: str):
    m = RE_TZ_SEARCH.search(text)
    if m:
        val = int(m.group(1))
        if -12 <= val <= 14:
            return val
    return resolve_tz_from_city(text)

# =====================================================================
# VERİ TABANI YÖNETİMİ
# =====================================================================
LANG_FILE = "user_langs.json"
TZ_FILE = "user_timezones.json"
TZ_LOCK_FILE = "user_tz_locked.json"
EXAMS_FILE = "user_exams.json"
REMINDERS_FILE = "user_reminders.json"
CITY_FILE = "user_cities.json"
COORDS_FILE = "user_coords.json"
PRAYER_NOTIFS_FILE = "user_prayer_notifs.json"
FRIDAY_NOTIFS_FILE = "user_friday_notifs.json"
MODULE_STATS_FILE = "module_stats.json"
MODULE_STATS = {
    "prayer": 0, "video": 0, "exam_todo": 0, "weather": 0,
    "pdf_hub": 0, "pomodoro": 0, "adhkar": 0, "translit": 0, "hadith": 0
}
GLOBAL_BOT = None
TODOS_FILE = "user_todos.json"
RECENT_CITIES_FILE = "user_recent_cities.json"
BANNED_USERS_FILE = "user_banned.json"
MAINTENANCE_FILE = "maintenance_state.json"
NIGHTLY_BACKUP_TRACK_FILE = "backup_last.json"
QAZA_FILE = "user_qaza.json"

BANNED_USERS = {}
MAINTENANCE_MODE = False
BROADCAST_ABORT_FLAG = False
NIGHTLY_BACKUP_TRACK = {}

USER_LANGS = {}
USER_TIMEZONES = {}
USER_TZ_LOCKED = {}
USER_EXAMS = {}
USER_REMINDERS = {}
USER_CITIES = {}
USER_COORDS = {}
USER_PRAYER_NOTIFS = {}
USER_FRIDAY_NOTIFS = {}
USER_TODOS = {}
USER_RECENT_CITIES = {}
USER_QAZA = {}
DAILY_PRAYER_CACHE = {}

def load_databases():
    global USER_LANGS, USER_TIMEZONES, USER_TZ_LOCKED, USER_EXAMS, USER_REMINDERS, USER_CITIES
    global USER_COORDS, USER_PRAYER_NOTIFS, USER_FRIDAY_NOTIFS, USER_TODOS, USER_RECENT_CITIES
    for fname, var_ref in [
        (LANG_FILE, USER_LANGS),
        (TZ_FILE, USER_TIMEZONES),
        (TZ_LOCK_FILE, USER_TZ_LOCKED),
        (EXAMS_FILE, USER_EXAMS),
        (REMINDERS_FILE, USER_REMINDERS),
        (CITY_FILE, USER_CITIES),
        (COORDS_FILE, USER_COORDS),
        (PRAYER_NOTIFS_FILE, USER_PRAYER_NOTIFS),
        (FRIDAY_NOTIFS_FILE, USER_FRIDAY_NOTIFS),
        (TODOS_FILE, USER_TODOS),
        (RECENT_CITIES_FILE, USER_RECENT_CITIES),
        (USER_PROFILES_FILE, USER_PROFILES),
        (BANNED_USERS_FILE, BANNED_USERS),
        (NIGHTLY_BACKUP_TRACK_FILE, NIGHTLY_BACKUP_TRACK),
        (QAZA_FILE, USER_QAZA),
        (MODULE_STATS_FILE, MODULE_STATS)
    ]:
        if os.path.exists(fname):
            try:
                with open(fname, "r", encoding="utf-8") as f:
                    var_ref.update(json.load(f))
            except Exception:
                pass
    load_admin_ids()
    global MAINTENANCE_MODE
    if os.path.exists(MAINTENANCE_FILE):
        try:
            with open(MAINTENANCE_FILE, "r", encoding="utf-8") as f:
                MAINTENANCE_MODE = json.load(f).get("active", False)
        except Exception:
            MAINTENANCE_MODE = False

def _write_json_to_disk(fname, payload_str):
    try:
        tmp_fname = f"{fname}.tmp"
        with open(tmp_fname, "w", encoding="utf-8") as f:
            f.write(payload_str)
        os.replace(tmp_fname, fname)
    except Exception as e:
        add_system_log(f"save_json disk hatasi ({fname}): {e}", level="WARN")

def save_json(fname, data):
    try:
        payload_str = json.dumps(data, ensure_ascii=False)
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _write_json_to_disk, fname, payload_str)
    except RuntimeError:
        payload_str = json.dumps(data, ensure_ascii=False)
        _write_json_to_disk(fname, payload_str)

def save_user_lang(user_id, lang_code: str):
    USER_LANGS[str(user_id)] = lang_code
    save_json(LANG_FILE, USER_LANGS)

def get_user_lang(user_id, context: ContextTypes.DEFAULT_TYPE = None) -> str:
    uid_str = str(user_id)
    if uid_str in USER_LANGS and USER_LANGS[uid_str] in TEXTS:
        return USER_LANGS[uid_str]
    if context and context.user_data and 'lang' in context.user_data:
        l = context.user_data['lang']
        if isinstance(l, str) and l in TEXTS:
            return l
    return 'uz'

def save_user_timezone(user_id, offset_hours: int, locked: bool = False):
    uid_str = str(user_id)
    USER_TIMEZONES[uid_str] = int(offset_hours)
    save_json(TZ_FILE, USER_TIMEZONES)
    if locked:
        USER_TZ_LOCKED[uid_str] = True
        save_json(TZ_LOCK_FILE, USER_TZ_LOCKED)

def save_user_city(user_id, city_name: str):
    uid_str = str(user_id)
    c_val = city_name.strip().title()
    USER_CITIES[uid_str] = c_val
    save_json(CITY_FILE, USER_CITIES)
    add_recent_city(user_id, c_val)

def add_recent_city(user_id: int, city_name: str):
    uid = str(user_id)
    if uid not in USER_RECENT_CITIES:
        USER_RECENT_CITIES[uid] = []
    c_clean = city_name.strip().title()
    if c_clean in USER_RECENT_CITIES[uid]:
        USER_RECENT_CITIES[uid].remove(c_clean)
    USER_RECENT_CITIES[uid].insert(0, c_clean)
    USER_RECENT_CITIES[uid] = USER_RECENT_CITIES[uid][:3]
    save_json(RECENT_CITIES_FILE, USER_RECENT_CITIES)

def get_recent_cities(user_id: int) -> list:
    return USER_RECENT_CITIES.get(str(user_id), [])

def get_user_city(user_id) -> str:
    return USER_CITIES.get(str(user_id))

# DÜZELTME 4: Konuşma metninden otomatik şehir arama kaldırıldı (Bölüm atlamalarını önleme)
def silent_background_tz_sync(user_id: int, raw_text: str = None, user_lang_code: str = None):
    uid_str = str(user_id)
    if USER_TZ_LOCKED.get(uid_str):
        return USER_TIMEZONES.get(uid_str, 3)

    detected_tz = None
    if raw_text:
        m_tz = RE_TZ_SEARCH.search(raw_text)
        if m_tz:
            val = int(m_tz.group(1))
            if -12 <= val <= 14:
                detected_tz = val

    if detected_tz is None and uid_str not in USER_TIMEZONES and user_lang_code:
        code = user_lang_code.lower()
        if code.startswith('tr'):
            detected_tz = 3
            save_user_city(user_id, "Istanbul")
        elif code.startswith('ru'):
            detected_tz = 3
            save_user_city(user_id, "Moskva")
        elif code.startswith('uz'):
            detected_tz = 5
            save_user_city(user_id, "Toshkent")

    if detected_tz is not None:
        save_user_timezone(user_id, detected_tz, locked=False)
        return detected_tz

    return USER_TIMEZONES.get(uid_str, 3)

def get_user_tz_offset(user_id, context: ContextTypes.DEFAULT_TYPE = None) -> int:
    uid_str = str(user_id)
    if uid_str in USER_TIMEZONES:
        return USER_TIMEZONES[uid_str]
    lang = get_user_lang(user_id, context)
    if lang in ('tr', 'ru', 'en'):
        return 3
    return 5

def get_user_now(user_id, context: ContextTypes.DEFAULT_TYPE = None) -> datetime:
    offset = get_user_tz_offset(user_id, context)
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    return utc_now + timedelta(hours=offset)

def add_user_exam(user_id, title: str, date_str: str) -> str:
    uid_str = str(user_id)
    if uid_str not in USER_EXAMS:
        USER_EXAMS[uid_str] = []
    exam_id = str(int(time.time() * 1000))[-6:]
    USER_EXAMS[uid_str].append({'id': exam_id, 'title': title, 'date': date_str})
    save_json(EXAMS_FILE, USER_EXAMS)
    return exam_id

def delete_user_exam(user_id, exam_id: str) -> bool:
    uid_str = str(user_id)
    if uid_str in USER_EXAMS:
        orig = len(USER_EXAMS[uid_str])
        USER_EXAMS[uid_str] = [e for e in USER_EXAMS[uid_str] if e.get('id') != exam_id]
        if len(USER_EXAMS[uid_str]) < orig:
            save_json(EXAMS_FILE, USER_EXAMS)
            return True
    return False

def get_user_exams(user_id) -> list:
    return USER_EXAMS.get(str(user_id), [])

def format_exam_countdown(exam_date_str: str, now: datetime, lang: str = 'uz') -> str:
    try:
        exam_dt = datetime.strptime(exam_date_str, '%Y-%m-%d %H:%M')
        diff = exam_dt - now
        if diff.total_seconds() <= 0:
            labels = {
                'uz': "Imtihon vaqti oʻtgan",
                'tr': "Sınav vakti geçti",
                'ru': "Время экзамена прошло",
                'en': "Exam date has passed"
            }
            return f"🏁 _{labels.get(lang, labels['uz'])}_"
        days = diff.days
        hours = diff.seconds // 3600
        mins = (diff.seconds % 3600) // 60
        if days > 0:
            lbl_days = {'uz': "kun", 'tr': "gün", 'ru': "дн.", 'en': "days"}.get(lang, "kun")
            lbl_hours = {'uz': "soat", 'tr': "saat", 'ru': "ч.", 'en': "hours"}.get(lang, "soat")
            lbl_left = {'uz': "qoldi", 'tr': "kaldı", 'ru': "осталось", 'en': "left"}.get(lang, "qoldi")
            return f"⏳ `{days} {lbl_days}, {hours} {lbl_hours} {lbl_left}`"
        else:
            lbl_hours = {'uz': "soat", 'tr': "saat", 'ru': "ч.", 'en': "hours"}.get(lang, "soat")
            lbl_mins = {'uz': "daq", 'tr': "dk", 'ru': "мин.", 'en': "mins"}.get(lang, "daq")
            lbl_left = {'uz': "qoldi", 'tr': "kaldı", 'ru': "осталось", 'en': "left"}.get(lang, "qoldi")
            return f"⏳ `{hours} {lbl_hours}, {mins} {lbl_mins} {lbl_left}`"
    except Exception:
        return ""

def add_user_reminder(user_id, text: str, target_dt_str: str) -> str:
    uid_str = str(user_id)
    if uid_str not in USER_REMINDERS:
        USER_REMINDERS[uid_str] = []
    rem_id = str(int(time.time() * 1000))[-6:]
    USER_REMINDERS[uid_str].append({'id': rem_id, 'text': text, 'time': target_dt_str})
    save_json(REMINDERS_FILE, USER_REMINDERS)
    return rem_id

def add_user_todo(user_id: int, text: str):
    uid = str(user_id)
    if uid not in USER_TODOS:
        USER_TODOS[uid] = []
    task_id = str(len(USER_TODOS[uid]) + 1)
    USER_TODOS[uid].append({'id': task_id, 'text': text.strip(), 'done': False})
    save_json(TODOS_FILE, USER_TODOS)

def toggle_user_todo(user_id: int, task_idx: int):
    uid = str(user_id)
    if uid in USER_TODOS and 0 <= task_idx < len(USER_TODOS[uid]):
        USER_TODOS[uid][task_idx]['done'] = not USER_TODOS[uid][task_idx]['done']
        save_json(TODOS_FILE, USER_TODOS)

def clear_completed_todos(user_id: int):
    uid = str(user_id)
    if uid in USER_TODOS:
        USER_TODOS[uid] = [t for t in USER_TODOS[uid] if not t['done']]
        for i, t in enumerate(USER_TODOS[uid]):
            t['id'] = str(i + 1)
        save_json(TODOS_FILE, USER_TODOS)

def get_user_todos(user_id: int) -> list:
    return USER_TODOS.get(str(user_id), [])

def get_user_reminders(user_id) -> list:
    return USER_REMINDERS.get(str(user_id), [])

def delete_user_reminder(user_id, rem_id: str):
    uid_str = str(user_id)
    if uid_str in USER_REMINDERS:
        USER_REMINDERS[uid_str] = [r for r in USER_REMINDERS[uid_str] if r.get('id') != rem_id]
        save_json(REMINDERS_FILE, USER_REMINDERS)

def save_user_coords(user_id, lat: float, lon: float, elevation: float = 0.0, country: str = ""):
    uid_str = str(user_id)
    USER_COORDS[uid_str] = {'lat': float(lat), 'lon': float(lon), 'elevation': float(elevation), 'country': country}
    save_json(COORDS_FILE, USER_COORDS)

def get_user_coords(user_id):
    return USER_COORDS.get(str(user_id))

def toggle_user_prayer_notif(user_id, mode: str = "on_time"):
    uid_str = str(user_id)
    if uid_str not in USER_PRAYER_NOTIFS:
        USER_PRAYER_NOTIFS[uid_str] = {"enabled": True, "offset": 0, "last": ""}
    
    if mode == "disable":
        USER_PRAYER_NOTIFS[uid_str]["enabled"] = False
    elif mode == "15min":
        USER_PRAYER_NOTIFS[uid_str]["enabled"] = True
        USER_PRAYER_NOTIFS[uid_str]["offset"] = 15
    else:
        USER_PRAYER_NOTIFS[uid_str]["enabled"] = True
        USER_PRAYER_NOTIFS[uid_str]["offset"] = 0
    save_json(PRAYER_NOTIFS_FILE, USER_PRAYER_NOTIFS)
    return USER_PRAYER_NOTIFS[uid_str]

def get_user_prayer_notif(user_id):
    return USER_PRAYER_NOTIFS.get(str(user_id), {"enabled": False, "offset": 0, "last": ""})

# DÜZELTME 3: Tüm geçici durumları ve bayrakları kökten sıfırlama

def factory_reset_user(user_id: int):
    uid_str = str(user_id)
    USER_EXAMS.pop(uid_str, None)
    USER_REMINDERS.pop(uid_str, None)
    USER_TODOS.pop(uid_str, None)
    USER_QAZA.pop(uid_str, None)
    USER_PRAYER_NOTIFS.pop(uid_str, None)
    USER_FRIDAY_NOTIFS.pop(uid_str, None)
    save_json(EXAMS_FILE, USER_EXAMS)
    save_json(REMINDERS_FILE, USER_REMINDERS)
    save_json(TODOS_FILE, USER_TODOS)
    save_json(QAZA_FILE, USER_QAZA)
    save_json(PRAYER_NOTIFS_FILE, USER_PRAYER_NOTIFS)
    save_json(FRIDAY_NOTIFS_FILE, USER_FRIDAY_NOTIFS)

async def wipe_chat_history(bot, chat_id: int, from_message_id: int, count: int = 80):
    tasks = []
    for mid in range(from_message_id, max(1, from_message_id - count), -1):
        tasks.append(bot.delete_message(chat_id=chat_id, message_id=mid))
    await asyncio.gather(*tasks, return_exceptions=True)

def cleanup_user_temp_files(context, user_id):
    if not context or not context.user_data:
        return
    dp = context.user_data.get('direct_file_path')
    if dp:
        try:
            if os.path.exists(dp):
                os.remove(dp)
        except Exception:
            pass
    context.user_data.pop('direct_file_path', None)
    context.user_data.pop('exam_draft_title', None)
    context.user_data.pop('exam_draft_dt', None)
    context.user_data.pop('prayer_candidates', None)
    context.user_data.pop('admin_ban_target', None)
    context.user_data.pop('admin_ban_mode', None)
    context.user_data.pop('admin_dm_target', None)
    context.user_data.pop('admin_broadcast_mode', None)
    context.user_data.pop('admin_search_mode', None)
    context.user_data.pop('admin_add_mode', None)
    context.user_data.pop('admin_restore_mode', None)
    context.user_data.pop('pending_broadcast_text', None)
    context.user_data.pop('broadcast_target_segment', None)
    context.user_data['mode'] = 'auto'

# =====================================================================
# KAZA NAMAZI TAKİPÇİSİ MOTORU
# =====================================================================
def get_user_qaza(user_id: int) -> dict:
    uid_str = str(user_id)
    if uid_str not in USER_QAZA:
        USER_QAZA[uid_str] = {"fajr": 0, "dhuhr": 0, "asr": 0, "maghrib": 0, "isha": 0, "witr": 0}
    return USER_QAZA[uid_str]

def update_user_qaza(user_id: int, prayer_name: str, delta: int = 1) -> dict:
    q = get_user_qaza(user_id)
    if prayer_name in q:
        q[prayer_name] = max(0, q[prayer_name] + delta)
        save_json(QAZA_FILE, USER_QAZA)
    return q

def reset_user_qaza(user_id: int) -> dict:
    uid_str = str(user_id)
    USER_QAZA[uid_str] = {"fajr": 0, "dhuhr": 0, "asr": 0, "maghrib": 0, "isha": 0, "witr": 0}
    save_json(QAZA_FILE, USER_QAZA)
    return USER_QAZA[uid_str]

# =====================================================================
# BAN, BAKIM, DİSK TEMİZLEME VE YEDEKLEME MOTORU
# =====================================================================
def ban_user(user_id: int, reason: str = "", mode: str = "soft") -> bool:
    global BANNED_USERS
    if user_id in ADMIN_IDS:
        return False
    BANNED_USERS[str(user_id)] = {
        "reason": reason or "Kural ihlali / Spam",
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }
    save_json(BANNED_USERS_FILE, BANNED_USERS)
    invalidate_users_cache()
    return True

def unban_user(user_id: int) -> bool:
    global BANNED_USERS
    uid_str = str(user_id)
    if uid_str in BANNED_USERS:
        BANNED_USERS.pop(uid_str, None)
        save_json(BANNED_USERS_FILE, BANNED_USERS)
        invalidate_users_cache()
        return True
    return False

def is_user_banned(user_id: int) -> bool:
    return str(user_id) in BANNED_USERS

def get_user_ban_info(user_id: int) -> dict:
    return BANNED_USERS.get(str(user_id), {})

async def execute_hard_ban(bot, target_uid: int, reason: str = ""):
    uid_str = str(target_uid)
    ban_user(target_uid, reason=reason, mode="hard")
    USER_PRAYER_NOTIFS.pop(uid_str, None)
    USER_FRIDAY_NOTIFS.pop(uid_str, None)
    USER_REMINDERS.pop(uid_str, None)
    save_json(PRAYER_NOTIFS_FILE, USER_PRAYER_NOTIFS)
    save_json(FRIDAY_NOTIFS_FILE, USER_FRIDAY_NOTIFS)
    save_json(REMINDERS_FILE, USER_REMINDERS)
    farewell_msg = (
        "⛔ *NUN PROJECT // ERİŞİM KALICI OLARAK SONLANDIRILDI*\n\n"
        "Bot kullanımınız ve erişiminiz kuralları ihlal ettiğiniz gerekçesiyle yönetim tarafından kalıcı olarak durdurulmuştur.\n\n"
        "Tüm bildirimleriniz ve bot menünüz tamamen kaldırılmıştır."
    )
    try:
        await bot.send_message(chat_id=target_uid, text=farewell_msg, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    except Exception:
        pass
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=target_uid))
    except Exception:
        pass

async def execute_soft_ban(bot, target_uid: int, reason: str = ""):
    ban_user(target_uid, reason=reason, mode="soft")
    notice_msg = (
        "⚠️ *NUN PROJECT // HESAP KISITLAMASI*\n\n"
        "Bot erişiminiz standart olarak kısıtlanmıştır. Komut ve butonlar geçici olarak devre dışıdır."
    )
    try:
        await bot.send_message(chat_id=target_uid, text=notice_msg, parse_mode="Markdown")
    except Exception:
        pass

def toggle_maintenance_mode() -> bool:
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    save_json(MAINTENANCE_FILE, {"active": MAINTENANCE_MODE})
    return MAINTENANCE_MODE

def is_maintenance_active() -> bool:
    return MAINTENANCE_MODE

def get_disk_usage_info() -> str:
    try:
        total, used, free = shutil.disk_usage("/")
        used_gb = used / (1024**3)
        total_gb = total / (1024**3)
        pct = (used / total) * 100
        return f"{used_gb:.1f} GB / {total_gb:.1f} GB (%{pct:.1f})"
    except Exception:
        return "Bilinmiyor"

def cleanup_orphaned_temp_files(max_age_seconds: int = 1800) -> float:
    freed_bytes = 0
    now = time.time()
    target_dirs = [tempfile.gettempdir(), "/tmp", os.getcwd()]
    for d in target_dirs:
        if os.path.exists(d):
            try:
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    if os.path.isfile(fp):
                        ext = os.path.splitext(f)[1].lower()
                        if ext in ('.mp4', '.jpg', '.jpeg', '.png', '.pdf', '.part', '.ytdl', '.tmp'):
                            try:
                                if now - os.path.getmtime(fp) >= max_age_seconds:
                                    sz = os.path.getsize(fp)
                                    os.remove(fp)
                                    freed_bytes += sz
                            except Exception:
                                pass
            except Exception:
                pass
    return freed_bytes / (1024 * 1024)

def create_database_backup_zip() -> str:
    timestamp_str = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    zip_filename = os.path.join(tempfile.gettempdir(), f"nun_bot_backup_{timestamp_str}.zip")
    db_files = [
        LANG_FILE, TZ_FILE, TZ_LOCK_FILE, EXAMS_FILE, REMINDERS_FILE, CITY_FILE,
        COORDS_FILE, PRAYER_NOTIFS_FILE, FRIDAY_NOTIFS_FILE, TODOS_FILE,
        RECENT_CITIES_FILE, USER_PROFILES_FILE, ADMINS_FILE, BANNED_USERS_FILE,
        MAINTENANCE_FILE, QAZA_FILE
    ]
    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in db_files:
            if os.path.exists(f):
                zf.write(f)
    return zip_filename

async def send_backup_to_admins(bot, trigger_type: str = "Manuel"):
    zip_path = create_database_backup_zip()
    if not os.path.exists(zip_path):
        return False
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    file_sz_kb = os.path.getsize(zip_path) / 1024.0
    caption = (
        f"💾 *NUN PROJECT // VERİTABANI YEDEK DOSYASI*\n\n"
        f"📌 Tür: `{trigger_type}`\n"
        f"📅 Tarih: `{now_str}`\n"
        f"📦 Boyut: `{file_sz_kb:.1f} KB`\n\n"
        f"_Tüm kullanıcılar, sınavlar, ezan bildirimleri ve to-do kayıtları güvenle paketlendi._"
    )
    success = False
    for aid in ADMIN_IDS:
        try:
            with open(zip_path, "rb") as doc_f:
                await bot.send_document(
                    chat_id=aid,
                    document=doc_f,
                    filename=os.path.basename(zip_path),
                    caption=caption,
                    parse_mode="Markdown"
                )
            success = True
        except Exception:
            pass
    try:
        os.remove(zip_path)
    except Exception:
        pass
    return success

async def nightly_maintenance_worker(app):
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        now_date_str = now.strftime("%Y-%m-%d")

        if now.hour == 3 and (0 <= now.minute <= 15):
            last_b = NIGHTLY_BACKUP_TRACK.get("last_nightly_backup", "")
            if last_b != now_date_str:
                NIGHTLY_BACKUP_TRACK["last_nightly_backup"] = now_date_str
                save_json(NIGHTLY_BACKUP_TRACK_FILE, NIGHTLY_BACKUP_TRACK)
                await send_backup_to_admins(app.bot, trigger_type="Otomatik Gece Yedeği (03:00)")

        if now.minute == 0:
            cleanup_orphaned_temp_files(max_age_seconds=1800)

async def notify_admin_audit_log(bot, actor_admin_id: int, action_text: str):
    actor_p = USER_PROFILES.get(str(actor_admin_id), {})
    actor_name = safe_md(actor_p.get('name') or f"Admin {actor_admin_id}")
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    log_card = (
        f"🛡️ *NUN PROJECT // GÜVENLİK VE DENETİM GÜNLÜĞÜ*\n\n"
        f"👤 *İşlemi Yapan:* {actor_name} (`{actor_admin_id}`)\n"
        f"📌 *İşlem:* {action_text}\n"
        f"⏰ *Zaman:* `{now_str}`"
    )
    for aid in ADMIN_IDS:
        if aid != actor_admin_id:
            try:
                await bot.send_message(chat_id=aid, text=log_card, parse_mode="Markdown")
            except Exception:
                pass

def restore_database_from_zip(zip_file_path: str) -> tuple:
    if not os.path.exists(zip_file_path) or not zipfile.is_zipfile(zip_file_path):
        return False, "Geçersiz veya bozuk ZIP dosyası."
    try:
        with zipfile.ZipFile(zip_file_path, "r") as zf:
            extracted_count = 0
            for name in zf.namelist():
                if name.endswith(".json"):
                    zf.extract(name, os.getcwd())
                    extracted_count += 1
        load_databases()
        invalidate_users_cache()
        return True, f"{extracted_count} adet veritabanı dosyası başarıyla yüklendi."
    except Exception as e:
        return False, str(e)

def search_registered_users(query_text: str) -> list:
    all_users = get_all_registered_users()
    q = query_text.lower().strip().lstrip("@")
    if not q:
        return []
    matches = []
    for u in all_users:
        uid_str = str(u['id'])
        name_str = (u.get('name') or '').lower()
        uname_str = (u.get('username') or '').lower()
        if q in uid_str or q in name_str or q in uname_str:
            matches.append(u)
    return matches

# =====================================================================
# PDF DÖNÜŞTÜRME MOTORU
# =====================================================================
def docx_to_pdf(input_docx: str, output_pdf: str) -> bool:
    try:
        doc = docx.Document(input_docx)
        styles = getSampleStyleSheet()
        story = []
        for p in doc.paragraphs:
            txt = p.text.strip()
            if not txt:
                story.append(Spacer(1, 8))
                continue
            if p.style.name.startswith('Heading'):
                story.append(Paragraph(f"<b>{txt}</b>", styles['Heading2']))
                story.append(Spacer(1, 6))
            else:
                story.append(Paragraph(txt, styles['Normal']))
                story.append(Spacer(1, 4))
        pdf_doc = SimpleDocTemplate(output_pdf, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        pdf_doc.build(story)
        return True
    except Exception as e:
        print(f"docx_to_pdf hatasi: {e}")
        return False

def xlsx_to_pdf(input_xlsx: str, output_pdf: str) -> bool:
    try:
        wb = openpyxl.load_workbook(input_xlsx, data_only=True)
        ws = wb.active
        table_data = []
        for row in ws.iter_rows(values_only=True):
            if any(cell is not None for cell in row):
                table_data.append([str(c) if c is not None else "" for c in row])
        if not table_data:
            return False
        pdf_doc = SimpleDocTemplate(output_pdf, pagesize=A4)
        story = [Paragraph("<b>Excel Document</b>", getSampleStyleSheet()['Heading2']), Spacer(1, 10)]
        t = Table(table_data)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#222222')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#666666')),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ]))
        story.append(t)
        pdf_doc.build(story)
        return True
    except Exception as e:
        print(f"xlsx_to_pdf hatasi: {e}")
        return False

def txt_to_pdf(input_txt: str, output_pdf: str) -> bool:
    try:
        with open(input_txt, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        doc = SimpleDocTemplate(output_pdf, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        story = [Paragraph("<b>Document</b>", styles['Heading2']), Spacer(1, 10)]
        for line in content.split("\n"):
            line_clean = line.strip()
            if line_clean:
                story.append(Paragraph(line_clean, styles['Normal']))
                story.append(Spacer(1, 4))
        doc.build(story)
        return True
    except Exception as e:
        print(f"txt_to_pdf hatasi: {e}")
        return False

# =====================================================================
# GELİŞMİŞ ÇOK DİLLİ OCR MOTORU
# =====================================================================
def extract_text_from_image(image_path: str) -> str:
    try:
        with Image.open(image_path) as raw_img:
            img_gray = raw_img.convert('L')
            w, h = img_gray.size
            if w < 1200 or h < 1200:
                scale = max(1200 / w, 1200 / h)
                img_gray = img_gray.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
            
            enhancer = ImageEnhance.Contrast(img_gray)
            img_enhanced = enhancer.enhance(1.8)

            avail = []
            try:
                avail = pytesseract.get_languages()
            except Exception:
                avail = ['eng']

            priority_langs = ['ara', 'chi_sim', 'chi_tra', 'tur', 'uzb', 'uzb_cyrl', 'rus', 'eng']
            active_langs = [l for l in priority_langs if l in avail]
            lang_arg = "+".join(active_langs) if active_langs else "eng"

            text = pytesseract.image_to_string(img_enhanced, lang=lang_arg, config=r'--psm 3')
            if not text.strip():
                text = pytesseract.image_to_string(raw_img, lang=lang_arg, config=r'--psm 3')

            return text.strip()
    except Exception as e:
        print(f"OCR hatasi: {e}")
        return ""

# =====================================================================
# ÇOK DİLLİ DERS PROGRAMI GÖRSELİ (PILLOW / 1080x1920)
# =====================================================================
def parse_schedule_text(text: str, user_lang: str = 'uz'):
    day_indices = {
        "dushanba": 0, "pazartesi": 0, "monday": 0, "понедельник": 0, "пн": 0, "mon": 0,
        "seshanba": 1, "sali": 1, "tuesday": 1, "вторник": 1, "вт": 1, "tue": 1,
        "chorshanba": 2, "carsamba": 2, "çarşamba": 2, "wednesday": 2, "среда": 2, "ср": 2, "wed": 2,
        "payshanba": 3, "persembe": 3, "perşembe": 3, "thursday": 3, "четверг": 3, "чт": 3, "thu": 3,
        "juma": 4, "cuma": 4, "friday": 4, "пятница": 4, "пт": 4, "fri": 4,
        "shanba": 5, "cumartesi": 5, "saturday": 5, "суббота": 5, "сб": 5, "sat": 5,
        "yakshanba": 6, "pazar": 6, "sunday": 6, "воскресенье": 6, "вс": 6, "sun": 6,
    }
    
    localized_days = {
        'uz': ["DUSHANBA", "SESHANBA", "CHORSHANBA", "PAYSHANBA", "JUMA", "SHANBA", "YAKSHANBA"],
        'tr': ["PAZARTESİ", "SALI", "ÇARŞAMBA", "PERŞEMBE", "CUMA", "CUMARTESİ", "PAZAR"],
        'ru': ["ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА", "ВОСКРЕСЕНЬЕ"],
        'en': ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
    }
    
    current_day = localized_days.get(user_lang, localized_days['uz'])[0]
    schedule = {}
    lines = text.strip().split("\n")
    for line in lines:
        l_c = line.strip()
        if not l_c:
            continue
        first_word = l_c.lower().replace(":", "").replace("-", "").split()[0]
        if first_word in day_indices:
            idx = day_indices[first_word]
            current_day = localized_days.get(user_lang, localized_days['uz'])[idx]
            if current_day not in schedule:
                schedule[current_day] = []
            rest = l_c[len(first_word):].lstrip(":- ").strip()
            if rest:
                schedule[current_day].append(rest)
        else:
            if current_day not in schedule:
                schedule[current_day] = []
            schedule[current_day].append(l_c)
    return schedule

def generate_schedule_wallpaper(schedule_data: dict, output_path: str, user_lang: str = 'uz'):
    width, height = 1080, 1920
    img = Image.new("RGB", (width, height), color=(10, 10, 10))
    draw = ImageDraw.Draw(img)

    titles = {
        'uz': ("AKADEMIK REJA", "DARS JADVALI", "NUN PROJECT • QULFLANGAN EKRAN JADVALI"),
        'tr': ("AKADEMİK PROGRAM", "DERS PROGRAMI", "NUN PROJECT • KİLİT EKRANI PROGRAMI"),
        'ru': ("УЧЕБНЫЙ ПЛАН", "РАСПИСАНИЕ ЗАНЯТИЙ", "NUN PROJECT • ЭКРАН БЛОКИРОВКИ"),
        'en': ("ACADEMIC TIMETABLE", "CLASS SCHEDULE", "NUN PROJECT • LOCKSCREEN TIMETABLE"),
    }
    sub_title, main_title, footer_text = titles.get(user_lang, titles['uz'])

    font_bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf"
    ]
    font_norm_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arial.ttf"
    ]
    bold_path = next((p for p in font_bold_candidates if os.path.exists(p)), None)
    norm_path = next((p for p in font_norm_candidates if os.path.exists(p)), None)

    font_large = font_med = font_sub = font_item = None
    if bold_path and norm_path:
        try:
            font_large = ImageFont.truetype(bold_path, 44)
            font_med = ImageFont.truetype(bold_path, 30)
            font_sub = ImageFont.truetype(norm_path, 24)
            font_item = ImageFont.truetype(norm_path, 26)
        except Exception:
            pass

    if font_large is None:
        font_large = font_med = font_sub = font_item = ImageFont.load_default()

    for x in range(0, width, 80):
        draw.line([(x, 0), (x, height)], fill=(18, 18, 18), width=1)
    for y in range(0, height, 80):
        draw.line([(0, y), (width, y)], fill=(18, 18, 18), width=1)

    draw.rectangle([(60, 80), (width - 60, 220)], fill=(18, 18, 18), outline=(60, 60, 60), width=2)
    draw.text((100, 105), f"NUN PROJECT // {sub_title}", fill=(160, 160, 160), font=font_sub)
    draw.text((100, 140), main_title, fill=(255, 255, 255), font=font_large)

    cur_y = 260
    for day, items in schedule_data.items():
        if cur_y > height - 200:
            break
        day_box_height = 55 + (len(items) * 45)
        draw.rectangle([(60, cur_y), (width - 60, cur_y + day_box_height)], fill=(15, 15, 15), outline=(50, 50, 50), width=1)
        draw.rectangle([(60, cur_y), (width - 60, cur_y + 45)], fill=(30, 30, 30))
        draw.text((80, cur_y + 8), f"●  {day}", fill=(255, 255, 255), font=font_med)

        item_y = cur_y + 55
        for it in items:
            draw.text((90, item_y + 5), f"▫️  {it}", fill=(220, 220, 220), font=font_item)
            item_y += 42
        cur_y += day_box_height + 20

    draw.text((width // 2 - 200, height - 70), footer_text, fill=(100, 100, 100), font=font_sub)
    img.save(output_path, quality=95)
    return True

def check_and_repair_databases() -> tuple:
    db_list = [
        (LANG_FILE, USER_LANGS),
        (TZ_FILE, USER_TIMEZONES),
        (TZ_LOCK_FILE, USER_TZ_LOCKED),
        (EXAMS_FILE, USER_EXAMS),
        (REMINDERS_FILE, USER_REMINDERS),
        (CITY_FILE, USER_CITIES),
        (COORDS_FILE, USER_COORDS),
        (PRAYER_NOTIFS_FILE, USER_PRAYER_NOTIFS),
        (FRIDAY_NOTIFS_FILE, USER_FRIDAY_NOTIFS),
        (TODOS_FILE, USER_TODOS),
        (RECENT_CITIES_FILE, USER_RECENT_CITIES),
        (USER_PROFILES_FILE, USER_PROFILES),
        (BANNED_USERS_FILE, BANNED_USERS),
        (NIGHTLY_BACKUP_TRACK_FILE, NIGHTLY_BACKUP_TRACK),
        (MAINTENANCE_FILE, None),
        (QAZA_FILE, USER_QAZA),
        (MODULE_STATS_FILE, MODULE_STATS)
    ]
    checked_count = 0
    repaired_count = 0
    total_records = 0

    for fname, var_ref in db_list:
        checked_count += 1
        if not os.path.exists(fname):
            save_json(fname, var_ref if var_ref is not None else {"active": False})
            repaired_count += 1
        else:
            try:
                with open(fname, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        raise ValueError("Boş dosya")
                    parsed = json.loads(content)
                    if var_ref is not None:
                        total_records += len(parsed)
            except Exception:
                fallback_data = var_ref if var_ref is not None else {"active": False}
                save_json(fname, fallback_data)
                repaired_count += 1

    for uid_str in list(USER_LANGS.keys()):
        if uid_str not in USER_PROFILES:
            try:
                uid_int = int(uid_str)
                USER_PROFILES[uid_str] = {
                    'id': uid_int,
                    'name': f"User {uid_int}",
                    'username': "",
                    'last_active': "Kayıtlı (Pasif)"
                }
            except Exception:
                pass
    save_json(USER_PROFILES_FILE, USER_PROFILES)
    invalidate_users_cache()

    status_msg = (
        "🩺 *NUN PROJECT // VERİTABANI SAĞLIK RAPORU*\n\n"
        f"📁 Taranan DB Dosyaları: `{checked_count}` adet\n"
        f"🛠️ Onarılan / Sıfırlanan: `{repaired_count}` adet\n"
        f"📊 Toplam Kayıtlı Veri Öğesi: `{total_records}`\n"
        f"👥 Eşitlenmiş Kullanıcı Profili: `{len(USER_PROFILES)}`\n\n"
        "✅ _Tüm JSON veri yapıları sağlıklı, eşzamanlı ve kullanıma hazırdır._"
    )
    add_system_log(f"DB Sağlık taraması tamamlandı: {checked_count} dosya kontrol edildi, {repaired_count} onarıldı.")
    return True, status_msg

async def daily_telemetry_worker(app):
    while True:
        await asyncio.sleep(45)
        now = datetime.now()
        now_date_str = now.strftime("%Y-%m-%d")
        if now.hour == 0 and (0 <= now.minute <= 10):
            last_t = NIGHTLY_BACKUP_TRACK.get("last_daily_telemetry", "")
            if last_t != now_date_str:
                NIGHTLY_BACKUP_TRACK["last_daily_telemetry"] = now_date_str
                save_json(NIGHTLY_BACKUP_TRACK_FILE, NIGHTLY_BACKUP_TRACK)
                all_u = get_all_registered_users()
                total_u = len(all_u)
                p_count = sum(1 for c_item in USER_PRAYER_NOTIFS.values() if c_item.get("enabled"))
                b_count = len(BANNED_USERS)
                ram_mb = get_ram_usage_mb()
                uptime_s = get_uptime_string()

                bulten = (
                    f"📊 *NUN PROJECT // GÜNLÜK YÖNETİCİ BÜLTENİ*\n"
                    f"📅 Tarih: `{now.strftime('%d.%m.%Y')}`\n\n"
                    f"▫️ Toplam Kayıtlı Kullanıcı: `{total_u}`\n"
                    f"▫️ Vakit Bildirimi Alanlar: `{p_count}`\n"
                    f"▫️ Engellenen Kullanıcılar: `{b_count}`\n"
                    f"▫️ Kesintisiz Çalışma (Uptime): `{uptime_s}`\n"
                    f"▫️ Bellek Kullanımı: `{ram_mb:.1f} MB`\n\n"
                    f"🟢 _Sistem kesintisiz ve sağlıklı çalışmaya devam ediyor._"
                )
                for aid in ADMIN_IDS:
                    try:
                        await app.bot.send_message(chat_id=aid, text=bulten, parse_mode="Markdown")
                    except Exception:
                        pass

def export_users_to_excel(output_path: str) -> bool:
    try:
        all_u = get_all_registered_users()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Kullanıcılar"

        headers = ["ID", "İsim", "Kullanıcı Adı", "Şehir", "Dil", "Saat Dilimi", "Ezan Bildirimi", "Durum", "Son Aktiflik"]
        ws.append(headers)

        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
            cell.fill = openpyxl.styles.PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
            cell.alignment = openpyxl.styles.Alignment(horizontal="center", vertical="center")

        for u in all_u:
            uid = u['id']
            is_adm = uid in ADMIN_IDS
            is_ban = is_user_banned(uid)
            if is_adm:
                st = "Yönetici"
            elif is_ban:
                st = "Engelli"
            else:
                st = "Aktif"

            row = [
                str(uid),
                u.get('name') or "",
                f"@{u['username']}" if u.get('username') else "",
                u.get('city') or "",
                (u.get('lang') or 'uz').upper(),
                f"UTC{'+' if (u.get('tz') or 0) >= 0 else ''}{u.get('tz') or 0}",
                "Açık" if u.get('prayer_active') else "Kapalı",
                st,
                u.get('last_active') or ""
            ]
            ws.append(row)

        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        wb.save(output_path)
        return True
    except Exception as e:
        add_system_log(f"Excel aktarma hatasi: {e}", level="ERROR")
        return False

# =====================================================================
# POMODORO & ARKA PLAN GÖREVLERİ
# =====================================================================
async def pomodoro_timer_task(bot, chat_id: int, duration_mins: int, is_break: bool, user_lang: str):
    await asyncio.sleep(duration_mins * 60)
    t = TEXTS.get(user_lang, TEXTS['uz'])
    if is_break:
        msg = f"🔔 *{t['pomo_break_over']}* ☕"
        btn_lbl = t['pomo_btn_work25']
        cb = "pomo_25"
    else:
        msg = f"🎉 *{t['pomo_work_over']}* 🍅 (`{duration_mins} {t['pomo_mins_unit']}`)"
        btn_lbl = t['pomo_btn_break5']
        cb = "pomo_5"

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(btn_lbl, callback_data=cb)]])
    try:
        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass

async def prayer_and_friday_worker(app):
    while True:
        await asyncio.sleep(30)
        now_utc = datetime.now(timezone.utc)
        today_utc_str = now_utc.strftime("%Y-%m-%d")
        
        old_cache_keys = [k for k in list(DAILY_PRAYER_CACHE.keys()) if not k.endswith(today_utc_str)]
        for k in old_cache_keys:
            DAILY_PRAYER_CACHE.pop(k, None)
        
        all_uids = set(USER_TIMEZONES.keys()) | set(USER_PRAYER_NOTIFS.keys()) | set(USER_FRIDAY_NOTIFS.keys())
        for uid_str in all_uids:
            try:
                uid = int(uid_str)
                user_now = get_user_now(uid)
                today_str = user_now.strftime("%Y-%m-%d")
                now_hm = user_now.strftime("%H:%M")
                u_lang = get_user_lang(uid)
                t = TEXTS.get(u_lang, TEXTS['uz'])

                if user_now.weekday() == 4 and (8 <= user_now.hour < 10):
                    last_fri = USER_FRIDAY_NOTIFS.get(uid_str, {}).get("last", "")
                    if last_fri != today_str:
                        if uid_str not in USER_FRIDAY_NOTIFS:
                            USER_FRIDAY_NOTIFS[uid_str] = {}
                        USER_FRIDAY_NOTIFS[uid_str]["last"] = today_str
                        save_json(FRIDAY_NOTIFS_FILE, USER_FRIDAY_NOTIFS)
                        try:
                            msg = f"🌙 *{t['friday_title']}*\n\n{t['friday_text']}"
                            await app.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                        except Exception:
                            pass

                p_cfg = USER_PRAYER_NOTIFS.get(uid_str)
                if p_cfg and p_cfg.get("enabled", False):
                    u_coords = get_user_coords(uid)
                    if u_coords:
                        c_key = f"{uid_str}_{today_str}"
                        if c_key in DAILY_PRAYER_CACHE:
                            timings = DAILY_PRAYER_CACHE[c_key]
                        else:
                            tz_off = get_user_tz_offset(uid)
                            calc = AstroPrayerTimes(u_coords['lat'], u_coords['lon'], elevation=u_coords.get('elevation', 0.0), tz_offset=float(tz_off), country_code=u_coords.get('country', ''))
                            timings = calc.calculate_times(user_now)
                            DAILY_PRAYER_CACHE[c_key] = timings

                        offset = p_cfg.get("offset", 0)
                        prayer_names = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha']
                        labels_p = {
                            'uz': {'Fajr': "BOMDOD", 'Dhuhr': "PESHIN", 'Asr': "ASR", 'Maghrib': "SHOM", 'Isha': "XUFTON"},
                            'tr': {'Fajr': "İMSAK / SABAH", 'Dhuhr': "ÖĞLE", 'Asr': "İKİNDİ", 'Maghrib': "AKŞAM / İFTAR", 'Isha': "YATSI"},
                            'ru': {'Fajr': "ФАДЖР", 'Dhuhr': "ЗУХР", 'Asr': "АСР", 'Maghrib': "МАГРИБ", 'Isha': "ИША"},
                            'en': {'Fajr': "FAJR", 'Dhuhr': "DHUHR", 'Asr': "ASR", 'Maghrib': "MAGHRIB", 'Isha': "ISHA"},
                        }
                        
                        target_dt = user_now + timedelta(minutes=offset)
                        target_hm = target_dt.strftime("%H:%M")
                        
                        for p in prayer_names:
                            p_time = timings.get(p)
                            if p_time and p_time == target_hm:
                                notif_id = f"{today_str}_{p}_{offset}"
                                if p_cfg.get("last") != notif_id:
                                    p_cfg["last"] = notif_id
                                    save_json(PRAYER_NOTIFS_FILE, USER_PRAYER_NOTIFS)
                                    p_lbl = labels_p.get(u_lang, labels_p['uz']).get(p, p)
                                    if offset > 0:
                                        alert_head = "⏰ 15 daqiqa qoldi" if u_lang=='uz' else ("⏰ 15 Dakika Kaldı" if u_lang=='tr' else ("⏰ Осталось 15 минут" if u_lang=='ru' else "⏰ 15 Minutes Left"))
                                        body = f"🕌 *NUN PROJECT // {alert_head}*\n\n📌 *[ {p_lbl} ]* `{p_time}`\n\n_{t['notif_verse']}_"
                                    else:
                                        body = f"🕌 *NUN PROJECT // {t['notif_alert_title']}*\n\n🔔 *[ {p_lbl} ]* {t['notif_entered']} (`{p_time}`)\n\n_{t['notif_verse']}_"
                                    try:
                                        await app.bot.send_message(chat_id=uid, text=body, parse_mode="Markdown")
                                    except Exception:
                                        pass
            except Exception:
                pass

async def reminders_worker(app):
    while True:
        await asyncio.sleep(25)
        for uid, items in list(USER_REMINDERS.items()):
            user_now = get_user_now(int(uid))
            now_str = user_now.strftime("%Y-%m-%d %H:%M")
            due = []
            for it in list(items):
                if it.get("time") <= now_str:
                    due.append(it)
                    items.remove(it)
            if due:
                save_json(REMINDERS_FILE, USER_REMINDERS)
                u_lang = USER_LANGS.get(str(uid), 'uz')
                t = TEXTS.get(u_lang, TEXTS['uz'])
                for item in due:
                    try:
                        await app.bot.send_message(
                            chat_id=int(uid),
                            text=f"🔔 *{t['remind_due']}*\n\n📌 *{t['remind_task_lbl']}:* {item.get('text')}\n⏰ {t['remind_note']}",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

# =====================================================================
# KUSURSUZ ÖZBEKÇE KİRİL <-> LATİN ÇEVİRİ MOTORU
# =====================================================================
APOSTROPHES = set(["'", "’", "‘", "`", "ʻ", "ʼ", "\u02bb", "\u02bc"])
VOWELS_CYR = set("аоуиэеёюяўАОУИЭЕЁЮЯЎ")
VOWELS_LAT = set("aouieAOUiE")

MAP_CYR_TO_LAT = {
    'А': 'A', 'а': 'a', 'Б': 'B', 'б': 'b', 'В': 'V', 'в': 'v',
    'Г': 'G', 'г': 'g', 'Д': 'D', 'д': 'd', 'Ж': 'J', 'ж': 'j',
    'З': 'Z', 'з': 'z', 'И': 'I', 'и': 'i', 'Й': 'Y', 'й': 'y',
    'К': 'K', 'к': 'k', 'Қ': 'Q', 'қ': 'q', 'Л': 'L', 'l': 'l',
    'М': 'M', 'm': 'm', 'Н': 'N', 'n': 'n', 'О': 'O', 'o': 'o',
    'П': 'P', 'p': 'p', 'Р': 'R', 'r': 'r', 'С': 'S', 's': 's',
    'Т': 'T', 't': 't', 'У': 'U', 'у': 'u', 'Ф': 'F', 'f': 'f',
    'Х': 'X', 'х': 'x', 'Ҳ': 'H', 'ҳ': 'h', 'Э': 'E', 'э': 'e',
}

MAP_LAT_TO_CYR = {
    'A': 'А', 'a': 'а', 'B': 'Б', 'b': 'б', 'V': 'В', 'v': 'в',
    'G': 'Г', 'g': 'г', 'D': 'Д', 'd': 'д', 'J': 'Ж', 'j': 'ж',
    'Z': 'З', 'z': 'з', 'I': 'И', 'i': 'и', 'Y': 'Й', 'y': 'й',
    'K': 'К', 'k': 'к', 'Q': 'Қ', 'q': 'қ', 'L': 'Л', 'l': 'l',
    'M': 'М', 'm': 'м', 'N': 'Н', 'n': 'н', 'O': 'О', 'o': 'о',
    'P': 'П', 'p': 'п', 'R': 'Р', 'r': 'р', 'S': 'С', 's': 'с',
    'T': 'Т', 't': 'т', 'U': 'У', 'u': 'у', 'F': 'Ф', 'f': 'ф',
    'X': 'Х', 'x': 'х', 'H': 'Ҳ', 'h': 'ҳ',
}

def cyrillic_to_latin(text: str) -> str:
    res = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        prev = text[i-1] if i > 0 else " "
        nxt = text[i+1] if i+1 < n else ""
        
        if c in ('С', 'с') and nxt in ('Ҳ', 'ҳ'):
            res.append("S'H" if (c.isupper() and nxt.isupper()) else ("S'h" if c.isupper() else "s'h"))
            i += 2
            continue
            
        if c in ('Е', 'е'):
            if i == 0 or not prev.isalpha() or prev in VOWELS_CYR or prev in 'ъЪьЬ':
                res.append("YE" if (c.isupper() and nxt.isupper()) else ("Ye" if c.isupper() else "ye"))
            else:
                res.append("E" if c.isupper() else "e")
            i += 1
            continue
            
        if c in ('Ё', 'ё'):
            res.append("YO" if (c.isupper() and nxt.isupper()) else ("Yo" if c.isupper() else "yo"))
            i += 1
            continue
        if c in ('Ю', 'ю'):
            res.append("YU" if (c.isupper() and nxt.isupper()) else ("Yu" if c.isupper() else "yu"))
            i += 1
            continue
        if c in ('Я', 'я'):
            res.append("YA" if (c.isupper() and nxt.isupper()) else ("Ya" if c.isupper() else "ya"))
            i += 1
            continue
            
        if c in ('Ч', 'ч'):
            res.append("CH" if (c.isupper() and nxt.isupper()) else ("Ch" if c.isupper() else "ch"))
            i += 1
            continue
            
        if c in ('Ш', 'ш', 'Щ', 'щ'):
            res.append("SH" if (c.isupper() and nxt.isupper()) else ("Sh" if c.isupper() else "sh"))
            i += 1
            continue
            
        if c in ('Ц', 'ц'):
            res.append("TS" if (c.isupper() and nxt.isupper()) else ("Ts" if c.isupper() else "ts"))
            i += 1
            continue
            
        if c == 'Ў':
            res.append("Oʻ")
            i += 1
            continue
        if c == 'ў':
            res.append("oʻ")
            i += 1
            continue
        if c == 'Ғ':
            res.append("Gʻ")
            i += 1
            continue
        if c == 'ғ':
            res.append("gʻ")
            i += 1
            continue
        if c in ('Ъ', 'ъ'):
            res.append("'")
            i += 1
            continue
        if c in ('Ь', 'ь'):
            i += 1
            continue
            
        res.append(MAP_CYR_TO_LAT.get(c, c))
        i += 1
        
    return "".join(res)

def latin_to_cyrillic(text: str) -> str:
    res = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i+1] if i+1 < n else ""
        nxt2 = text[i+2] if i+2 < n else ""
        prev = text[i-1] if i > 0 else " "
        
        if c in ('s', 'S') and nxt in APOSTROPHES and nxt2 in ('h', 'H'):
            res.append("СҲ" if (c.isupper() and nxt2.isupper()) else ("Сҳ" if c.isupper() else "сҳ"))
            i += 3
            continue

        if c in ('y', 'Y') and nxt in ('o', 'O') and nxt2 in APOSTROPHES:
            res.append("ЙЎ" if (c.isupper() and nxt2.isupper()) else ("Йў" if c.isupper() else "йў"))
            i += 3
            continue
            
        if c in ('o', 'O') and nxt in APOSTROPHES:
            res.append("Ў" if c.isupper() else "ў")
            i += 2
            continue
            
        if c in ('g', 'G') and nxt in APOSTROPHES:
            res.append("Ғ" if c.isupper() else "ғ")
            i += 2
            continue
            
        if c in ('s', 'S') and nxt in ('h', 'H'):
            res.append("Ш" if (c.isupper() and nxt.isupper()) else ("Ш" if c.isupper() else "ш"))
            i += 2
            continue
            
        if c in ('c', 'C') and nxt in ('h', 'H'):
            res.append("Ч" if (c.isupper() and nxt.isupper()) else ("Ch" if c.isupper() else "ch"))
            i += 2
            continue
            
        if c in ('t', 'T') and nxt in ('s', 'S'):
            res.append("Ц" if (c.isupper() and nxt.isupper()) else ("Ц" if c.isupper() else "ц"))
            i += 2
            continue
            
        if c in ('y', 'Y') and nxt in ('o', 'O'):
            res.append("Ё" if (c.isupper() and nxt.isupper()) else ("Ё" if c.isupper() else "ё"))
            i += 2
            continue
            
        if c in ('y', 'Y') and nxt in ('u', 'U'):
            res.append("Ю" if (c.isupper() and nxt.isupper()) else ("Ю" if c.isupper() else "ю"))
            i += 2
            continue
            
        if c in ('y', 'Y') and nxt in ('a', 'A'):
            res.append("Я" if (c.isupper() and nxt.isupper()) else ("Я" if c.isupper() else "я"))
            i += 2
            continue
            
        if c in ('y', 'Y') and nxt in ('e', 'E'):
            res.append("Е" if (c.isupper() and nxt.isupper()) else ("Е" if c.isupper() else "е"))
            i += 2
            continue
            
        if c in ('e', 'E'):
            if i == 0 or not prev.isalpha() or prev.lower() in VOWELS_LAT:
                res.append("Э" if c.isupper() else "э")
            else:
                res.append("Е" if c.isupper() else "е")
            i += 1
            continue
            
        if c in APOSTROPHES:
            res.append("ъ" if prev.isalpha() else "'")
            i += 1
            continue
            
        res.append(MAP_LAT_TO_CYR.get(c, c))
        i += 1
        
    return "".join(res)

def is_mostly_cyrillic(text: str) -> bool:
    return len(RE_CYRILLIC_LETTERS.findall(text)) >= len(RE_LATIN_LETTERS.findall(text))

# =====================================================================
# YÜKSEK HASSASİYETLİ ASTRONOMİK VE FIKHİ NAMAZ & KERAHAT MOTORU
# =====================================================================
def clean_time_str(val: str) -> str:
    m = RE_CLEAN_TIME.search(str(val))
    return m.group(0) if m else "--:--"

class AstroPrayerTimes:
    def __init__(self, lat: float, lon: float, elevation: float = 0.0, tz_offset: float = 5.0, country_code: str = 'UZ'):
        self.lat = lat
        self.lon = lon
        self.elevation = max(0.0, elevation)
        self.tz_offset = tz_offset
        self.country_code = (country_code or '').upper()

    def _julian_date(self, year: int, month: int, day: int) -> float:
        if month <= 2:
            year -= 1
            month += 12
        A = math.floor(year / 100.0)
        B = 2 - A + math.floor(A / 4.0)
        return math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + B - 1524.5

    def _sun_coordinates(self, jd: float):
        d = jd - 2451545.0
        g = (357.529 + 0.98560028 * d) % 360.0
        q = (280.459 + 0.98564736 * d) % 360.0
        L = (q + 1.915 * math.sin(math.radians(g)) + 0.020 * math.sin(math.radians(2 * g))) % 360.0
        e = 23.439 - 0.00000036 * d
        sin_dec = math.sin(math.radians(e)) * math.sin(math.radians(L))
        dec = math.degrees(math.asin(sin_dec))
        ra = math.degrees(math.atan2(math.cos(math.radians(e)) * math.sin(math.radians(L)), math.cos(math.radians(L)))) / 15.0
        ra = (ra + 24.0) % 24.0
        eqt = (q / 15.0) - ra
        if eqt > 12.0: eqt -= 24.0
        if eqt < -12.0: eqt += 24.0
        return dec, eqt

    def calculate_times(self, date: datetime) -> dict:
        jd = self._julian_date(date.year, date.month, date.day)
        dec, eqt = self._sun_coordinates(jd)
        noon = 12.0 + self.tz_offset - (self.lon / 15.0) - eqt
        dip = 0.0347 * math.sqrt(self.elevation) if self.elevation > 0 else 0.0
        sun_rise_angle = 0.833 + dip

        def hour_angle(alpha: float) -> float:
            lat_r = math.radians(self.lat)
            dec_r = math.radians(dec)
            num = -math.sin(math.radians(alpha)) - (math.sin(lat_r) * math.sin(dec_r))
            denom = math.cos(lat_r) * math.cos(dec_r)
            val = num / denom
            if val > 1.0: return 0.0
            if val < -1.0: return 180.0
            return math.degrees(math.acos(val))

        shadow_factor = 2.0
        asr_alt = math.degrees(math.atan(1.0 / (shadow_factor + math.tan(math.radians(abs(self.lat - dec))))))
        
        w_sunrise = hour_angle(sun_rise_angle) / 15.0
        w_fajr = hour_angle(18.0) / 15.0
        w_isha = hour_angle(17.0) / 15.0
        
        lat_r = math.radians(self.lat)
        dec_r = math.radians(dec)
        asr_val = (math.sin(math.radians(asr_alt)) - (math.sin(lat_r) * math.sin(dec_r))) / (math.cos(lat_r) * math.cos(dec_r))
        asr_val = max(-1.0, min(1.0, asr_val))
        w_asr = math.degrees(math.acos(asr_val)) / 15.0

        night_length = (24.0 - 2 * w_sunrise) if w_sunrise > 0 else 12.0
        if w_fajr == 0.0 or w_fajr == 12.0 or (noon - w_fajr) > (noon - w_sunrise):
            fajr_h = noon - w_sunrise - (night_length / 7.0)
        else:
            fajr_h = noon - w_fajr

        if w_isha == 0.0 or w_isha == 12.0:
            isha_h = noon + w_sunrise + (night_length / 7.0)
        else:
            isha_h = noon + w_isha

        if self.country_code == 'TR':
            dhuhr_temkin = 5.0 / 60.0
            asr_temkin = 4.0 / 60.0
            maghrib_temkin = 7.0 / 60.0
            isha_temkin = 2.0 / 60.0
        else:
            dhuhr_temkin = 2.0 / 60.0
            asr_temkin = 2.0 / 60.0
            maghrib_temkin = 3.0 / 60.0
            isha_temkin = 1.0 / 60.0

        sunrise_h = noon - w_sunrise
        dhuhr_h = noon + dhuhr_temkin
        asr_h = noon + w_asr + asr_temkin
        sunset_h = noon + w_sunrise + maghrib_temkin
        isha_h += isha_temkin
        
        def fmt(h: float) -> str:
            h = (h + 24.0) % 24.0
            hours = int(h)
            minutes = int(round((h - hours) * 60.0))
            if minutes == 60:
                hours = (hours + 1) % 24
                minutes = 0
            return f"{hours:02d}:{minutes:02d}"

        return {
            "Fajr": fmt(fajr_h),
            "Sunrise": fmt(sunrise_h),
            "Dhuhr": fmt(dhuhr_h),
            "Asr": fmt(asr_h),
            "Maghrib": fmt(sunset_h),
            "Isha": fmt(isha_h)
        }

def calculate_kerahat_and_israq(timings: dict, now_dt: datetime) -> dict:
    ref_d = now_dt.date()
    def to_dt(t_str: str) -> datetime:
        m = re.search(r"(\d{1,2}):(\d{2})", str(t_str))
        if m:
            return datetime(ref_d.year, ref_d.month, ref_d.day, int(m.group(1)), int(m.group(2)))
        return now_dt

    sunrise_dt = to_dt(timings.get("Sunrise", "06:00"))
    dhuhr_dt = to_dt(timings.get("Dhuhr", "12:30"))
    maghrib_dt = to_dt(timings.get("Maghrib", "18:30"))

    israq_dt = sunrise_dt + timedelta(minutes=45)
    istiva_start_dt = dhuhr_dt - timedelta(minutes=40)
    isfirar_start_dt = maghrib_dt - timedelta(minutes=45)

    is_kerahat = False
    current_k = None
    if sunrise_dt <= now_dt < israq_dt:
        is_kerahat = True
        current_k = "morning"
    elif istiva_start_dt <= now_dt < dhuhr_dt:
        is_kerahat = True
        current_k = "noon"
    elif isfirar_start_dt <= now_dt < maghrib_dt:
        is_kerahat = True
        current_k = "evening"

    return {
        "israq": israq_dt.strftime("%H:%M"),
        "k1_start": sunrise_dt.strftime("%H:%M"),
        "k1_end": israq_dt.strftime("%H:%M"),
        "k2_start": istiva_start_dt.strftime("%H:%M"),
        "k2_end": dhuhr_dt.strftime("%H:%M"),
        "k3_start": isfirar_start_dt.strftime("%H:%M"),
        "k3_end": maghrib_dt.strftime("%H:%M"),
        "is_kerahat": is_kerahat,
        "current_k": current_k
    }

def normalize_location_query(text: str) -> str:
    t = text.strip()
    cyr_map = {
        'қ': 'q', 'Қ': 'Q', 'ғ': 'g', 'Ғ': 'G', 'ў': 'o', 'Ў': 'O', 'ҳ': 'h', 'Ҳ': 'H',
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'j',
        'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
        'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 'ts',
        'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ъ': '', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
    }
    for cyr, lat in cyr_map.items():
        t = t.replace(cyr, lat)
    return re.sub(r"['’`ʻʼ\-]", "", t).strip()

def resolve_iana_offset(tz_name: str, lat: float, lon: float) -> float:
    if tz_name:
        try:
            zi = zoneinfo.ZoneInfo(tz_name)
            return datetime.now(timezone.utc).astimezone(zi).utcoffset().total_seconds() / 3600.0
        except Exception:
            pass
    return float(coords_to_tz_offset(lat, lon))

async def fetch_prayer_times_by_coord(lat: float, lon: float, elevation: float, name: str, admin1: str, country: str, tz_name: str = "", user_id: int = None):
    tz_offset = resolve_iana_offset(tz_name, lat, lon)
    parts = [p for p in [name, admin1, country] if p]
    display_name = ", ".join(parts[:2])
    
    if user_id:
        save_user_timezone(user_id, int(round(tz_offset)), locked=True)
        save_user_city(user_id, display_name)
        save_user_coords(user_id, lat, lon, elevation or 0.0, country)

    headers = {"User-Agent": "NunBot-Prayer/3.0"}
    try:
        aladhan_url = f"https://api.aladhan.com/v1/timings?latitude={lat}&longitude={lon}&method=13&school=1"
        client = get_http_client()
        resp = await client.get(aladhan_url, headers=headers)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            timings = data.get("timings", {})
            d = data.get("date", {})
            return timings, display_name, d.get("readable", ""), d.get("hijri", {}).get("date", ""), "AlAdhan / Diyanet Mezonlari"
    except Exception:
        pass

    calc = AstroPrayerTimes(lat, lon, elevation=elevation or 0.0, tz_offset=float(tz_offset), country_code=country)
    now_dt = datetime.now()
    timings = calc.calculate_times(now_dt)
    date_str = now_dt.strftime("%d %b %Y")
    return timings, display_name, date_str, "", "Diyanet & OʻMI Standarti (Astronomik Hisob)"

async def fetch_prayer_times(city_input: str, user_id: int = None, user_lang: str = 'uz'):
    norm_q = normalize_location_query(city_input)
    headers = {"User-Agent": "NunBot-Prayer/3.0"}
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(norm_q)}&count=5&language=en&format=json"
    
    candidates = []
    try:
        client = get_http_client()
        resp = await client.get(geo_url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                def rank_res(item):
                    cc = (item.get("country_code") or "").upper()
                    if user_lang == 'uz' and cc == 'UZ': return 0
                    if user_lang == 'tr' and cc == 'TR': return 0
                    if user_lang == 'ru' and cc in ('RU', 'UZ', 'KZ', 'KG', 'TJ', 'AZ'): return 0
                    return 1
                results.sort(key=rank_res)
                seen = set()
                for r in results:
                    nm = r.get("name", city_input.title())
                    ad = r.get("admin1", "")
                    co = r.get("country", "")
                    key = (nm.lower(), ad.lower(), co.lower())
                    if key not in seen:
                        seen.add(key)
                        candidates.append({
                            'lat': float(r["latitude"]),
                            'lon': float(r["longitude"]),
                            'elevation': float(r.get("elevation", 0.0)),
                            'name': nm,
                            'admin1': ad,
                            'country': co,
                            'timezone': r.get("timezone", "")
                        })
    except Exception as e:
        print(f"[GEO_PRAYER_ERROR] {e}")

    if not candidates:
        c_low = norm_q.lower()
        if c_low in CITY_TIMEZONE_MAP or resolve_tz_from_city(c_low) is not None:
            DEFAULT_COORDS = {
                "toshkent": (41.2995, 69.2401, 450), "samarqand": (39.6270, 66.9749, 700),
                "buxoro": (39.7681, 64.4556, 225), "andijon": (40.7821, 72.3442, 490),
                "namangan": (40.9983, 71.6726, 450), "fargona": (40.3842, 71.7843, 590),
                "qoqon": (40.5333, 70.9333, 400), "urganch": (41.5500, 60.6333, 90),
                "nukus": (42.4602, 59.6166, 75), "qarshi": (38.8606, 65.7891, 375),
                "navoiy": (40.0844, 65.3792, 380), "termiz": (37.2242, 67.2783, 300),
                "istanbul": (41.0082, 28.9784, 40), "ankara": (39.9334, 32.8597, 938),
                "moskva": (55.7558, 37.6173, 150),
            }
            if c_low in DEFAULT_COORDS:
                lt, ln, el = DEFAULT_COORDS[c_low]
                candidates.append({
                    'lat': lt, 'lon': ln, 'elevation': el,
                    'name': city_input.title(), 'admin1': '',
                    'country': 'Uzbekistan' if user_lang=='uz' else 'Turkey',
                    'timezone': ''
                })

    if not candidates:
        return None, None, None, None, None, None

    if len(candidates) > 1:
        return None, candidates, None, None, None, "MULTIPLE"

    best = candidates[0]
    timings, d_name, dt_s, h_s, src = await fetch_prayer_times_by_coord(
        best['lat'], best['lon'], best['elevation'], best['name'], best['admin1'], best['country'], best['timezone'], user_id=user_id
    )
    return timings, d_name, dt_s, h_s, src, "SINGLE"

def get_next_prayer_info(timings: dict, now: datetime, lang: str = 'uz') -> str:
    labels = {
        'uz': {'Fajr': "Bomdod (Saharlik)", 'Sunrise': "Quyosh", 'Dhuhr': "Peshin", 'Asr': "Asr", 'Maghrib': "Shom (Iftorlik)", 'Isha': "Xufton", 'to': "vaqtiga", 'left': "qoldi", 'hrs': "soat", 'mins': "daq"},
        'tr': {'Fajr': "İmsak (Sahur sonu)", 'Sunrise': "Güneş", 'Dhuhr': "Öğle", 'Asr': "İkindi", 'Maghrib': "Akşam (İftar)", 'Isha': "Yatsı", 'to': "vaktine", 'left': "kaldı", 'hrs': "saat", 'mins': "dk"},
        'ru': {'Fajr': "Фаджр (Сухур)", 'Sunrise': "Восход", 'Dhuhr': "Зухр", 'Asr': "Аср", 'Maghrib': "Магриб (Ифтар)", 'Isha': "Иша", 'to': "до времени", 'left': "осталось", 'hrs': "ч.", 'mins': "мин."},
        'en': {'Fajr': "Fajr (Suhoor)", 'Sunrise': "Sunrise", 'Dhuhr': "Dhuhr", 'Asr': "Asr", 'Maghrib': "Maghrib (Iftar)", 'Isha': "Isha", 'to': "until", 'left': "left", 'hrs': "hrs", 'mins': "mins"}
    }
    t_map = labels.get(lang, labels['uz'])
    today_date = now.date()
    prayer_order = ['Fajr', 'Sunrise', 'Dhuhr', 'Asr', 'Maghrib', 'Isha']
    upcoming = []
    for p in prayer_order:
        t_val = timings.get(p)
        if not t_val: continue
        parts = t_val.split(":")
        h, m = int(parts[0]), int(parts[1][:2])
        p_dt = datetime(today_date.year, today_date.month, today_date.day, h, m)
        if p_dt > now:
            upcoming.append((p, p_dt))

    if not upcoming:
        t_fajr = timings.get('Fajr', "05:00")
        parts = t_fajr.split(":")
        h, m = int(parts[0]), int(parts[1][:2])
        tomorrow = today_date + timedelta(days=1)
        next_dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, h, m)
        next_prayer = 'Fajr'
    else:
        next_prayer, next_dt = upcoming[0]

    diff = next_dt - now
    total_seconds = int(diff.total_seconds())
    diff_h = total_seconds // 3600
    diff_m = (total_seconds % 3600) // 60
    p_name = t_map[next_prayer]

    if lang == 'ru':
        return f"⏳ {t_map['to']} *{p_name}*: `{diff_h} {t_map['hrs']} {diff_m} {t_map['mins']} {t_map['left']}`"
    elif lang == 'en':
        return f"⏳ `{diff_h} {t_map['hrs']} {diff_m} {t_map['mins']} {t_map['left']}` {t_map['to']} *{p_name}*"
    else:
        return f"⏳ *{p_name}* {t_map['to']}: `{diff_h} {t_map['hrs']} {diff_m} {t_map['mins']} {t_map['left']}`"

def format_prayer_card(display_name: str, timings: dict, date_str: str, hijri_str: str, source: str, lang: str = 'uz', user_now: datetime = None, user_id: int = None):
    t_f = clean_time_str(timings.get("Fajr"))
    t_s = clean_time_str(timings.get("Sunrise"))
    t_d = clean_time_str(timings.get("Dhuhr"))
    t_a = clean_time_str(timings.get("Asr"))
    t_m = clean_time_str(timings.get("Maghrib"))
    t_i = clean_time_str(timings.get("Isha"))

    if not user_now:
        user_now = datetime.now()

    k_data = calculate_kerahat_and_israq(timings, user_now)

    if lang == 'tr':
        lbls = ("İMSAK", "GÜNEŞ", "ÖĞLE", "İKİNDİ", "AKŞAM", "YATSI")
        hdr = "*NUN PROJECT // NAMAZ VAKİTLERİ*"
        lbl_israq = "🌅 İşrak (Kuşluk Başlangıcı)"
        lbl_kerahat_head = "⚠️ Fıkhî Kerahat Vakitleri (Nafile/Kaza Mekruh)"
        lbl_k1 = "▫️ Tulû' (Gündoğumu)"
        lbl_k2 = "▫️ İstivâ (Öğle Öncesi)"
        lbl_k3 = "▫️ İsfirâr (Güneş Batışı)"
    elif lang == 'ru':
        lbls = ("ФАДЖР", "ВОСХОД", "ЗУХР", "АСР", "МАГРИБ", "ИША")
        hdr = "*NUN PROJECT // ВРЕМЯ НАМАЗА*"
        lbl_israq = "🌅 Время Ишрак (Начало Духа)"
        lbl_kerahat_head = "⚠️ Времена Карахат (Нежелательные для намаза)"
        lbl_k1 = "▫️ Тулю' (После восхода)"
        lbl_k2 = "▫️ Истива (Перед зухром)"
        lbl_k3 = "▫️ Исфирар (Перед закатом)"
    elif lang == 'en':
        lbls = ("FAJR", "SUNRISE", "DHUHR", "ASR", "MAGHRIB", "ISHA")
        hdr = "*NUN PROJECT // PRAYER TIMES*"
        lbl_israq = "🌅 Ishraq (Duha Start Time)"
        lbl_kerahat_head = "⚠️ Makruh Prayer Times (Prohibited for Nafl)"
        lbl_k1 = "▫️ Tulu' (Post-Sunrise)"
        lbl_k2 = "▫️ Istiwa (Pre-Dhuhr Zenith)"
        lbl_k3 = "▫️ Isfirar (Pre-Sunset)"
    else:
        lbls = ("BOMDOD", "QUYOSH", "PESHIN", "ASR", "SHOM", "XUFTON")
        hdr = "*NUN PROJECT // NAMOZ VAQTLARI*"
        lbl_israq = "🌅 Ishroq (Choshgoh boshlanishi)"
        lbl_kerahat_head = "⚠️ Fiqhiy Karohiyat Vaqtlari (Namoz makruh)"
        lbl_k1 = "▫️ Tulu' (Quyosh chiqqach)"
        lbl_k2 = "▫️ Istivo (Peshindan oldin)"
        lbl_k3 = "▫️ Isfirot (Quyosh botishidan oldin)"

    countdown_str = get_next_prayer_info(timings, user_now, lang)

    kerahat_status_alert = ""
    if k_data.get("is_kerahat"):
        warn_lbl = {
            'tr': "⚠️ *Şu anda Kerahat Vaktindesiniz! (Nafile namaz kılınmaz)*",
            'uz': "⚠️ *Hozir Karohiyat vaqti! (Nafl namoz oʻqilmaydi)*",
            'ru': "⚠️ *Сейчас время карахат! (Нафиль намаз не совершается)*",
            'en': "⚠️ *Currently in Makruh Time! (Nafl prayers prohibited)*"
        }.get(lang, "⚠️ Kerahat Vakti")
        kerahat_status_alert = f"\n{warn_lbl}\n"

    return (
        f"{hdr}\n"
        f"📍 *[ {display_name.upper()} ]*\n"
        f"📅 `{date_str}`\n\n"
        f"┌────────────────────────────┐\n"
        f"  ▫️ *{lbls[0]}:*    `{t_f}`\n"
        f"  ▫️ *{lbls[1]}:*    `{t_s}`\n"
        f"  ▫️ *{lbls[2]}:*    `{t_d}`\n"
        f"  ▫️ *{lbls[3]}:*    `{t_a}`\n"
        f"  ▫️ *{lbls[4]}:*    `{t_m}`\n"
        f"  ▫️ *{lbls[5]}:*    `{t_i}`\n"
        f"└────────────────────────────┘\n"
        f"{lbl_israq}: `{k_data['israq']}`\n\n"
        f"{lbl_kerahat_head}:\n"
        f"  {lbl_k1}: `{k_data['k1_start']} - {k_data['k1_end']}`\n"
        f"  {lbl_k2}: `{k_data['k2_start']} - {k_data['k2_end']}`\n"
        f"  {lbl_k3}: `{k_data['k3_start']} - {k_data['k3_end']}`\n"
        f"{kerahat_status_alert}\n"
        f"{countdown_str}\n\n"
        f"_{source}_"
    )

def get_kerahat_detailed_guide(lang: str = 'uz') -> str:
    guides = {
        'tr': (
            "📖 *İBADET FIKHI: KERAHAT VAKİTLERİ VE İŞRAK NAMAZI*\n\n"
            "İslam fıkhında (özellikle Hanefî ve Şâfiî mezheplerinde) günün 3 vaktinde farz, kaza veya nafile namaz kılmak tahrîmen (harama yakın) mekruh sayılmıştır:\n\n"
            "1️⃣ *Tulû' (Gündoğumu Kerahati):*\n"
            "Güneşin doğmasından itibaren yaklaşık *45 dakika* sürer. Bu vakitte hiçbir namaz kılınmaz. Bu 45 dakika bitince *İşrak (Duhâ / Kuşluk) vakti* girer.\n\n"
            "2️⃣ *İstivâ (Zeval Kerahati):*\n"
            "Güneş tam tepe noktasındayken, öğle ezanından önceki yaklaşık *40 dakikalık* süredir. Cuma günü dahil nafile kılınması mekruhtur.\n\n"
            "3️⃣ *İsfirâr (Güneş Batışı Kerahati):*\n"
            "Güneşin sararıp gözleri kamaştırmaz hale geldiği, akşam ezanından önceki *45 dakikalık* süredir. Bu vakitte sadece o günün henüz kılınmamış ikindi namazının farzı kılınabilir; nafile veya diğer kaza namazları kılınamaz.\n\n"
            "✨ *İşrak Namazının Fazileti:*\n"
            "Peygamber Efendimiz (s.a.v.) buyurmuştur:\n"
            "_«Kim sabah namazını cemaatle kıldıktan sonra güneş doğuncaya kadar oturup Allah'ı zikreder, sonra iki rekat (İşrak) namaz kılarsa, tam bir hac ve umre sevabı alır.»_ (Tirmizî)"
        ),
        'uz': (
            "📖 *IBODAT FIQHI: KAROHIYAT VAQTLARI VA ISHROQ NAMOZI*\n\n"
            "Islom fiqhida (xususan Hanafiy va Shofeʼiy mazhablarida) kun davomida 3 vaqtda har qanday nafl yoki qazo namozlarini oʻqish tahriman (haromga yaqin) makruh sanaladi:\n\n"
            "1️⃣ *Tulu' (Quyosh chiqish karohiyati):*\n"
            "Quyosh chiqqanidan boshlab taxminan *45 daqiqa* davom etadi. Bu vaqtda namoz oʻqilmaydi. Ushbu vaqt tugagach, *Ishroq (Choshgoh) vaqti* boshlanadi.\n\n"
            "2️⃣ *Istivo (Peshin oldi karohiyati):*\n"
            "Quyosh ayni tepaga kelgan, peshin azonidan oldingi taxminan *40 daqiqalik* vaqt. Bu vaqtda nafl oʻqish makruhdir.\n\n"
            "3️⃣ *Isfirot (Quyosh botishi oldi karohiyati):*\n"
            "Quyosh sargʻayib, koʻzni qamashtirmay qolgan, shom azonidan oldingi *45 daqiqa*. Bu vaqtda faqat oʻsha kunning qolib ketgan asr namozi farzini oʻqish mumkin, boshqa namozlar makruhdir.\n\n"
            "✨ *Ishroq namozining fazilati:*\n"
            "Rasululloh (s.a.v.) marhamat qilganlar:\n"
            "_«Kim bomdod namozini oʻqib, soʻng quyosh chiqquncha Allohni zikr qilsa, keyin ikki rakat (Ishroq) namozi oʻqisa, unga toʻliq haj va umra savobi beriladi.»_ (Termiziy)"
        ),
        'ru': (
            "📖 *ФИКХ ПОКЛОНЕНИЯ: ВРЕМЕНА КАРАХАТ И НАМАЗ ИШРАК*\n\n"
            "В исламском праве совершение добровольных (нафиль) и долговых намазов считается крайне нежелательным (макрух тахриман) в следующие 3 периода:\n\n"
            "1️⃣ *Тулю' (После восхода солнца):*\n"
            "Длится около *45 минут* после появления солнечного диска. По окончании этого времени наступает благословенное время намаза *Ишрак (Духа)*.\n\n"
            "2️⃣ *Истива (Зенит перед полуднем):*\n"
            "Около *40 минут* до наступления времени зухра, когда солнце находится в наивысшей точке.\n\n"
            "3️⃣ *Исфирар (Перед закатом солнца):*\n"
            "Около *45 минут* до времени магриба, когда солнце желтеет. В это время разрешается совершить только обязательный (фард) аср-намаз текущего дня, если он был упущен.\n\n"
            "✨ *Достоинство намаза Ишрак:*\n"
            "Пророк Мухаммад (мир ему и благословение) сказал:\n"
            "_«Кто совершит утренний намаз, затем останется поминать Аллаха до восхода солнца, а затем совершит два ракаата намаза, тот получит награду, подобную награде за полноценный хадж и умру.»_ (Тирмизи)"
        ),
        'en': (
            "📖 *FIQH OF WORSHIP: MAKRUH TIMES & ISHRAQ PRAYER*\n\n"
            "In Islamic jurisprudence, performing voluntary (nafl) or missed prayers is strictly disliked (tahriman makruh) during three specific times of the day:\n\n"
            "1️⃣ *Tulu' (Post-Sunrise):*\n"
            "Lasts approximately *45 minutes* after the sun rises. No prayers should be performed during this window. Once this window ends, *Ishraq (Duha) time* begins.\n\n"
            "2️⃣ *Istiwa (Zenith / Pre-Dhuhr):*\n"
            "Approximately *40 minutes* before Dhuhr prayer when the sun is at its meridian peak.\n\n"
            "3️⃣ *Isfirar (Pre-Sunset):*\n"
            "Approximately *45 minutes* before Maghrib when the sun turns pale. Only the obligatory (Fard) Asr prayer of that specific day may be performed if delayed; all other prayers are disliked.\n\n"
            "✨ *Virtue of Ishraq Prayer:*\n"
            "The Prophet (peace be upon him) said:\n"
            "_«Whoever prays Fajr, then sits remembering Allah until the sun rises, then prays two rak'ahs, will have a reward like that of Hajj and 'Umrah, complete, complete, complete.»_ (Tirmidhi)"
        )
    }
    return guides.get(lang, guides['uz'])

# =====================================================================
# SAHİH HADİS-İ ŞERİF KOLEKSİYONU (BUHÂRÎ, MÜSLİM, RİYÂZÜ'S-SÂLİHÎN)
# =====================================================================
SAHIH_HADITHS = [
    {
        "id": 1,
        "source": "Sahih al-Bukhari (1), Sahih Muslim (1907)",
        "arabic": "إِنَّمَا الأَعْمَالُ بِالنِّيَّاتِ، وَإِنَّمَا لِكُلِّ امْرِئٍ مَا نَوَى",
        "uz": "«Albatta, amallar niyatlarga bogʻliqdir va har bir kishiga faqat niyat qilgani beriladi.»",
        "tr": "«Ameller ancak niyetlere göredir ve her kişi için ancak niyet ettiği şey vardır.»",
        "ru": "«Поистине, дела оцениваются только по намерениям, и каждому человеку достанется лишь то, что он намеревался обрести.»",
        "en": "«Actions are judged by intentions, and every person will get what they intended.»"
    },
    {
        "id": 2,
        "source": "Sahih al-Bukhari (13), Sahih Muslim (45)",
        "arabic": "لاَ يُؤْمِنُ أَحَدُكُمْ حَتَّى يُحِبَّ لأَخِيهِ مَا يُحِبُّ لِنَفْسِهِ",
        "uz": "«Sizlardan birortangiz oʻzi uchun yaxshi koʻrgan narsani birodari uchun ham yaxshi koʻrmaguncha haqiqiy moʻmin boʻla olmaydi.»",
        "tr": "«Sizden biriniz, kendisi için arzu ettiğini kardeşi için de arzu etmedikçe (tam anlamıyla) iman etmiş olmaz.»",
        "ru": "«Не уверует никто из вас до тех пор, пока не пожелает брату своему того же, чего желает самому себе.»",
        "en": "«None of you truly believes until he loves for his brother what he loves for himself.»"
    },
    {
        "id": 3,
        "source": "Sahih al-Bukhari (6011), Sahih Muslim (2321)",
        "arabic": "مَنْ كَانَ يُؤْمِنُ بِاللَّهِ وَالْيَوْمِ الآخِرِ فَلْيَقُلْ خَيْرًا أَوْ لِيَصْمُتْ",
        "uz": "«Kim Allohga va oxirat kuniga iymon keltirgan boʻlsa, faqat yaxshi soʻz aytsin yoki jim tursin!»",
        "tr": "«Kim Allah'a ve ahiret gününe iman ediyorsa, ya hayır söylesin ya da sussun!»",
        "ru": "«Кто верует в Аллаха и в Последний день, пусть говорит благое или молчит.»",
        "en": "«Whoever believes in Allah and the Last Day, let him speak good or remain silent.»"
    },
    {
        "id": 4,
        "source": "Sahih Muslim (2699)",
        "arabic": "مَنْ سَلَكَ طَرِيقًا يَلْتَمِسُ فِيهِ عِلْمًا سَهَّلَ اللَّهُ لَهُ بِهِ طَرِيقًا إِلَى الْجَنَّةِ",
        "uz": "«Kim ilm talab qilish yoʻliga qadam qoʻysa, Alloh taolo u tufayli unga jannat yoʻlini osonlashtiradi.»",
        "tr": "«Kim ilim öğrenmek için bir yola çıkarsa, Allah ona cennete giden yolu kolaylaştırır.»",
        "ru": "«Тому, кто встал на путь в поисках знаний, Аллах облегчит путь в Рай.»",
        "en": "«Whoever treads a path in search of knowledge, Allah will make easy for him the path to Paradise.»"
    },
    {
        "id": 5,
        "source": "Sahih al-Bukhari (6018), Sahih Muslim (2564)",
        "arabic": "الرَّاحِمُونَ يَرْحَمُهُمُ الرَّحْمَنُ، ارْحَمُوا مَنْ فِي الأَرْضِ يَرْحَمْكُمْ مَنْ فِي السَّمَاءِ",
        "uz": "«Rahm qiluvchilarga Rahmon boʻlgan Alloh rahm qiladi. Yerdagilarga rahm qiling, shunda osmondagi Zot sizga rahm qiladi.»",
        "tr": "«Merhamet edenlere Rahmân da merhamet eder. Siz yerdekilere merhamet edin ki, gökteki de size merhamet etsin.»",
        "ru": "«Милостивый помилует милосердных. Будьте милосердны к тем, кто на земле, и вас помилует Тот, Кто на небесах.»",
        "en": "«The merciful are shown mercy by the Most Merciful. Be merciful to those on earth, and the One in the heavens will be merciful to you.»"
    },
    {
        "id": 6,
        "source": "Sahih al-Bukhari (5027)",
        "arabic": "خَيْرُكُمْ مَنْ تَعَلَّمَ الْقُرْآنَ وَعَلَّمَهُ",
        "uz": "«Sizlarning eng yaxshingiz Qurʼonni oʻrganib, uni boshqalarga oʻrgatganingizdir.»",
        "tr": "«Sizin en hayırlınız, Kur'an'ı öğrenen ve öğretendir.»",
        "ru": "«Лучшими из вас являются те, кто изучал Коран и обучал ему других.»",
        "en": "«The best among you are those who learn the Quran and teach it.»"
    },
    {
        "id": 7,
        "source": "Sahih al-Bukhari (6412), Sahih Muslim (2687)",
        "arabic": "كَلِمَتَانِ خَفِيفَتَانِ عَلَى اللِّسَانِ، ثَقِيلَتَانِ فِي الْمِيزَانِ، حَبِيبَتَانِ إِلَى الرَّحْمَنِ: سُبْحَانَ اللَّهِ وَبِحَمْدِهِ، سُبْحَانَ اللَّهِ الْعَظِيمِ",
        "uz": "«Ikkita kalima borki, ular tilga yengil, tarozida ogʻir va Rahmonga suyuklidir: Subhanallohi va bihamdihi, Subhanallohil ʼAziym.»",
        "tr": "«Dile hafif, mizanda ağır, Rahmân'a sevgili iki kelime vardır: Sübhânallâhi ve bihamdihî, Sübhânallâhil azîm.»",
        "ru": "«Два слова легки для языка, тяжелы на весах и любимы Милостивым: 'Пречист Аллах и хвала Ему, Пречист Аллах Великий'.»",
        "en": "«Two phrases are light on the tongue, heavy on the scale, and beloved to the Most Merciful: 'Subhanallahi wa bihamdihi, Subhanallahil-Azim'.»"
    },
    {
        "id": 8,
        "source": "Sahih al-Bukhari (6407)",
        "arabic": "مَثَلُ الَّذِي يَذْكُرُ رَبَّهُ وَالَّذِي لاَ يَذْكُرُ رَبَّهُ مَثَلُ الْحَىِّ وَالْمَيِّتِ",
        "uz": "«Rabbini zikr qiluvchi bilan zikr qilmaydigan kimsaning misoli tirik bilan oʻlikning misoli kabidir.»",
        "tr": "«Rabbini zikreden kimse ile zikretmeyen kimsenin misali, diri ile ölü gibidir.»",
        "ru": "«Тот, кто поминает своего Господа, и тот, кто не поминает Его, подобны живому и мертвому.»",
        "en": "«The example of the one who remembers his Lord and the one who does not is like the living and the dead.»"
    },
    {
        "id": 9,
        "source": "Sahih al-Bukhari (6416), Sahih Muslim (2742)",
        "arabic": "نِعْمَتَانِ مَغْبُونٌ فِيهِمَا كَثِيرٌ مِنَ النَّاسِ: الصِّحَّةُ وَالْفَرَاغُ",
        "uz": "«Ikki ulugʻ neʼmat borki, koʻp odamlar undan gʻaflatda qolib, aldanib qoladilar: sogʻlik va boʻsh vaqt.»",
        "tr": "«İki nimet vardır ki, insanların çoğu bunların kıymetini bilmekte aldanmıştır: Sağlık ve boş vakit.»",
        "ru": "«Двух благ лишены многие люди: здоровья и свободного времени.»",
        "en": "«There are two blessings which many people lose: health and free time for doing good.»"
    },
    {
        "id": 10,
        "source": "Sahih Muslim (2564)",
        "arabic": "الْمُسْلِمُ مَنْ سَلِمَ الْمُسْلِمُونَ مِنْ لِسَانِهِ وَيَدِهِ",
        "uz": "«Haqiqiy musulmon – boshqa musulmonlar uning tili va qoʻlidan omonlikda boʻlgan kishidir.»",
        "tr": "«Müslüman, dilinden ve elinden diğer Müslümanların emin olduğu kimsedir.»",
        "ru": "«Мусульманин — это тот, от языка и рук которого находятся в безопасности другие мусульмане.»",
        "en": "«A true Muslim is the one from whose tongue and hands other Muslims are safe.»"
    },
    {
        "id": 11,
        "source": "Sahih al-Bukhari (6116), Sahih Muslim (47)",
        "arabic": "تَبَسُّمُكَ فِي وَجْهِ أَخِيكَ لَكَ صَدَقَةٌ",
        "uz": "«Birodaring yuziga tabassum bilan boqishing ham sen uchun bir sadaqadir.»",
        "tr": "«Kardeşine tebessüm etmen senin için bir sadakadır.»",
        "ru": "«Твоя улыбка в лицо брату твоему — это милостыня.»",
        "en": "«Your smile in the face of your brother is charity for you.»"
    },
    {
        "id": 12,
        "source": "Sahih al-Bukhari (24)",
        "arabic": "الْحَيَاءُ لاَ يَأْتِي إِلاَّ بِخَيْرٍ",
        "uz": "«Hayo faqatgina yaxshilik keltiradi.»",
        "tr": "«Haya, sadece ve sadece hayır getirir.»",
        "ru": "«Стыдливость не приносит ничего, кроме блага.»",
        "en": "«Modesty does not bring anything except good.»"
    }
]

def get_daily_hadith(now: datetime, offset: int = 0) -> tuple:
    day_num = now.timetuple().tm_yday
    idx = (day_num + offset) % len(SAHIH_HADITHS)
    return SAHIH_HADITHS[idx], idx

def format_daily_hadith_card(hadith: dict, now: datetime, lang: str = 'uz') -> str:
    headers = {
        'uz': "*NUN PROJECT // KUNNING SAHIH HADISI*",
        'tr': "*NUN PROJECT // GÜNÜN SAHİH HADİS-İ ŞERİFİ*",
        'ru': "*NUN PROJECT // ДОСТОВЕРНЫЙ ХАДИС ДНЯ*",
        'en': "*NUN PROJECT // DAILY SAHIH HADITH*"
    }
    hdr = headers.get(lang, headers['uz'])
    date_str = now.strftime("%d.%m.%Y")
    ar = hadith['arabic']
    translation = hadith.get(lang, hadith['uz'])
    source = hadith['source']
    
    return (
        f"{hdr}\n"
        f"📅 `{date_str}`\n\n"
        f"{ar}\n\n"
        f"{translation}\n\n"
        f"📚 *{source}*"
    )

# =====================================================================
# HİCRİ TAKVİM VE DİNİ GÜNLER / ETKİNLİKLER (2026 - 2027)
# =====================================================================
ISLAMIC_EVENTS = [
    ("2026-01-15", {'uz': "Isro va Meʼroj kechasi", 'tr': "Mirac Kandili", 'ru': "Ночь Мирадж", 'en': "Laylat al-Mi'raj"}),
    ("2026-02-02", {'uz': "Barot kechasi", 'tr': "Berat Kandili", 'ru': "Ночь Бараат", 'en': "Laylat al-Bara'at"}),
    ("2026-02-18", {'uz': "Muborak Ramazon oyining 1-kuni", 'tr': "Ramazan Başlangıcı (İlk Oruç)", 'ru': "Начало месяца Рамадан", 'en': "First Day of Ramadan"}),
    ("2026-03-16", {'uz': "Qadr kechasi (Laylatul Qadr)", 'tr': "Kadir Gecesi", 'ru': "Ночь аль-Кадр", 'en': "Laylat al-Qadr"}),
    ("2026-03-20", {'uz': "Ramazon hayiti (Iyd al-Fitr)", 'tr': "Ramazan Bayramı (1. Gün)", 'ru': "Праздник Ураза-байрам", 'en': "Eid al-Fitr"}),
    ("2026-05-26", {'uz': "Arafa kuni", 'tr': "Kurban Arefe Günü", 'ru': "День Арафа", 'en': "Day of Arafah"}),
    ("2026-05-27", {'uz': "Qurbon hayiti (Iyd al-Adha)", 'tr': "Kurban Bayramı (1. Gün)", 'ru': "Праздник Курбан-байрам", 'en': "Eid al-Adha"}),
    ("2026-06-16", {'uz': "Yangi Hijriy 1448-yil", 'tr': "Hicri Yılbaşı (1 Muharrem 1448)", 'ru': "Мусульманский Новый год (1448 г.х.)", 'en': "Islamic New Year (1448 AH)"}),
    ("2026-06-25", {'uz': "Ashuro kuni", 'tr': "Aşure Günü", 'ru': "День Ашура", 'en': "Day of Ashura"}),
    ("2026-08-25", {'uz': "Mavlid kechasi", 'tr': "Mevlid Kandili", 'ru': "Мавлид ан-Наби", 'en': "Mawlid al-Nabi"}),
    ("2026-12-10", {'uz': "Muborak Uch Oylar (1 Rajab)", 'tr': "Üç Ayların Başlangıcı (1 Recep)", 'ru': "Начало трех священных месяцев (Раджаб)", 'en': "Beginning of Three Holy Months"}),
    ("2026-12-17", {'uz': "Ragʻoib kechasi", 'tr': "Regaib Kandili", 'ru': "Ночь Рагаиб", 'en': "Laylat al-Raghaib"}),
    ("2027-01-05", {'uz': "Isro va Meʼroj kechasi", 'tr': "Mirac Kandili", 'ru': "Ночь Мирадж", 'en': "Laylat al-Mi'raj"}),
    ("2027-01-22", {'uz': "Barot kechasi", 'tr': "Berat Kandili", 'ru': "Ночь Бараат", 'en': "Laylat al-Bara'at"}),
    ("2027-02-08", {'uz': "Muborak Ramazon oyining 1-kuni", 'tr': "Ramazan Başlangıcı", 'ru': "Начало месяца Рамадан", 'en': "First Day of Ramadan"}),
    ("2027-03-06", {'uz': "Qadr kechasi", 'tr': "Kadir Gecesi", 'ru': "Ночь аль-Кадр", 'en': "Laylat al-Qadr"}),
    ("2027-03-10", {'uz': "Ramazon hayiti", 'tr': "Ramazan Bayramı", 'ru': "Праздник Ураза-байрам", 'en': "Eid al-Fitr"}),
    ("2027-05-16", {'uz': "Qurbon hayiti", 'tr': "Kurban Bayramı", 'ru': "Праздник Курбан-байрам", 'en': "Eid al-Adha"}),
]

def format_islamic_calendar_card(now: datetime, lang: str = 'uz') -> str:
    today = now.date()
    headers = {
        'uz': "*NUN PROJECT // HIJRIY TAQVIM VA DINIY KUNLAR*",
        'tr': "*NUN PROJECT // HİCRİ TAKVİM VE DİNİ GÜNLER*",
        'ru': "*NUN PROJECT // МУСУЛЬМАНСКИЙ КАЛЕНДАРЬ И ПРАЗДНИКИ*",
        'en': "*NUN PROJECT // ISLAMIC CALENDAR & HOLY DAYS*",
    }
    subheads = {
        'uz': "Yaqinlashib kelayotgan muborak kunlar va kechalar:",
        'tr': "Yaklaşan mübarek gün ve geceler:",
        'ru': "Ближайшие священные дни и ночи:",
        'en': "Upcoming blessed days and nights:",
    }
    lbl_left = {'uz': "kun qoldi", 'tr': "gün kaldı", 'ru': "дн. осталось", 'en': "days left"}
    lbl_today = {'uz': "BUGUN!", 'tr': "BUGÜN!", 'ru': "СЕГОДНЯ!", 'en': "TODAY!"}
    
    lines = [headers.get(lang, headers['uz']), f"📅 `{today.strftime('%d.%m.%Y')}`\n", f"_{subheads.get(lang, subheads['uz'])}_\n"]
    upcoming_count = 0
    for dt_str, names in ISLAMIC_EVENTS:
        ev_date = datetime.strptime(dt_str, "%Y-%m-%d").date()
        diff = (ev_date - today).days
        if diff >= 0 and upcoming_count < 6:
            name = names.get(lang, names['uz'])
            cd_str = f"🎉 *{lbl_today.get(lang, lbl_today['uz'])}*" if diff == 0 else f"⏳ `{diff} {lbl_left.get(lang, lbl_left['uz'])}`"
            lines.append(f"▫️ *{name}*\n   📅 `{ev_date.strftime('%d.%m.%Y')}` — {cd_str}\n")
            upcoming_count += 1
    return "\n".join(lines)

def generate_imsakiye_pdf(output_path: str, location_name: str, lat: float, lon: float, elevation: float, tz_offset: float, country_code: str, lang: str = 'uz'):
    doc = SimpleDocTemplate(output_path, pagesize=A4, rightMargin=25, leftMargin=25, topMargin=25, bottomMargin=25)
    styles = getSampleStyleSheet()
    
    headers_lang = {
        'uz': ("30 KUNLIK NAMOZ VA IMSOKIYA TAQVIMI", "NUN PROJECT // AKADEMIK VA IBODAT MARKAZI",
               ["Sana", "Kun", "Bomdod\n(Saharlik)", "Quyosh", "Peshin", "Asr", "Shom\n(Iftorlik)", "Xufton"]),
        'tr': ("30 GÜNLÜK NAMAZ VE İMSAKİYE VAKİTLERİ", "NUN PROJECT // DİJİTAL ASİSTAN",
               ["Tarih", "Gün", "İmsak\n(Sahur)", "Güneş", "Öğle", "İkindi", "Akşam\n(İftar)", "Yatsı"]),
        'ru': ("РАСПИСАНИЕ НАМАЗА НА 30 ДНЕЙ", "NUN PROJECT // ЦЕНТР ЗНАНИЙ",
               ["Дата", "День", "Фаджр\n(Сухур)", "Восход", "Зухр", "Аср", "Магриб\n(Ифтар)", "Иша"]),
        'en': ("30-DAY PRAYER & RAMADAN TIMETABLE", "NUN PROJECT // DIGITAL COMPANION",
               ["Date", "Day", "Fajr\n(Suhoor)", "Sunrise", "Dhuhr", "Asr", "Maghrib\n(Iftar)", "Isha"]),
    }
    h_title, sub_title, col_names = headers_lang.get(lang, headers_lang['uz'])
    days_short = {
        'uz': ["Du", "Se", "Cho", "Pa", "Ju", "Sha", "Ya"],
        'tr': ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"],
        'ru': ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
        'en': ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    }
    
    calc = AstroPrayerTimes(lat, lon, elevation=elevation, tz_offset=tz_offset, country_code=country_code)
    table_data = [col_names]
    start_date = datetime.now().date()
    for i in range(30):
        cur_d = start_date + timedelta(days=i)
        dt = datetime(cur_d.year, cur_d.month, cur_d.day)
        times = calc.calculate_times(dt)
        day_str = days_short.get(lang, days_short['uz'])[cur_d.weekday()]
        row = [cur_d.strftime("%d.%m"), day_str, times['Fajr'], times['Sunrise'], times['Dhuhr'], times['Asr'], times['Maghrib'], times['Isha']]
        table_data.append(row)
        
    story = []
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=15, leading=19, alignment=1, textColor=colors.HexColor('#111111'))
    sub_style = ParagraphStyle('DocSub', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=13, alignment=1, textColor=colors.HexColor('#555555'))
    story.append(Paragraph(f"<b>{h_title}</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"📍 {location_name.upper()} | {sub_title}", sub_style))
    story.append(Spacer(1, 10))
    
    col_widths = [55, 45, 74, 60, 60, 60, 74, 60]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e3a5f')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d0d7de')),
    ]
    for row_idx in range(1, len(table_data)):
        if row_idx % 2 == 0:
            t_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor('#f6f8fa')))
        cur_d = start_date + timedelta(days=row_idx-1)
        if cur_d.weekday() == 4:
            t_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor('#e8f4ea')))
            t_style.append(('TEXTCOLOR', (1, row_idx), (1, row_idx), colors.HexColor('#137333')))
    t.setStyle(TableStyle(t_style))
    story.append(t)
    story.append(Spacer(1, 8))
    footer_style = ParagraphStyle('DocFooter', parent=styles['Normal'], fontName='Helvetica-Oblique', fontSize=8, leading=10, alignment=1, textColor=colors.HexColor('#777777'))
    story.append(Paragraph("Ehl-i Sünnet / Diyanet ve OʻMI standart astronomik mezonlari bilan tayyorlandi.", footer_style))
    doc.build(story)
    return True

# =====================================================================
# TO-DO VE MENÜ KLAVYELERİ
# =====================================================================
def format_todo_card(user_id: int, now: datetime, lang: str = 'uz'):
    tasks = get_user_todos(user_id)
    headers = {
        'uz': "*NUN PROJECT // KUNLIK DARS REJASI VA VAZIFALAR*",
        'tr': "*NUN PROJECT // GÜNLÜK DERS HEDEFLERİ (TO-DO)*",
        'ru': "*NUN PROJECT // ПЛАН И ЗАДАЧИ НА ДЕНЬ (TO-DO)*",
        'en': "*NUN PROJECT // DAILY STUDY GOALS (TO-DO)*"
    }
    hdr = headers.get(lang, headers['uz'])
    date_str = now.strftime("%d.%m.%Y")
    
    if not tasks:
        empty_lbl = {
            'uz': "Sizda bugun uchun faol vazifalar yoʻq.\nQuyidagi tugma orqali yangi maqsad qoʻshing:",
            'tr': "Bugün için henüz bir ders hedefi eklemediniz.\nAşağıdaki butonla yeni hedef ekleyin:",
            'ru': "У вас пока нет задач на сегодня.\nДобавьте цель с помощью кнопки ниже:",
            'en': "You have no study tasks for today yet.\nAdd a new goal using the button below:"
        }
        return f"{hdr}\n📅 `{date_str}`\n\n_{empty_lbl.get(lang, empty_lbl['uz'])}_"

    lines = [hdr, f"📅 `{date_str}`\n"]
    completed_cnt = sum(1 for t in tasks if t['done'])
    for idx, t in enumerate(tasks):
        icon = "✅" if t['done'] else "⬜"
        lines.append(f"{icon} `[{idx+1}]` {safe_md(t['text'])}")
        
    pct = int((completed_cnt / len(tasks)) * 100) if tasks else 0
    summary_lbl = {
        'uz': f"\n📊 Jami: {len(tasks)} ta | Bajarildi: {completed_cnt} ta (`{pct}%`)",
        'tr': f"\n📊 Toplam: {len(tasks)} | Tamamlanan: {completed_cnt} (`{pct}%`)",
        'ru': f"\n📊 Всего: {len(tasks)} | Выполнено: {completed_cnt} (`{pct}%`)",
        'en': f"\n📊 Total: {len(tasks)} | Done: {completed_cnt} (`{pct}%`)"
    }
    lines.append(summary_lbl.get(lang, summary_lbl['uz']))
    return "\n".join(lines)

def build_todo_keyboard(user_id: int, lang: str = 'uz'):
    tasks = get_user_todos(user_id)
    rows = []
    if tasks:
        toggle_row = []
        for idx, it in enumerate(tasks[:5]):
            icon = "☑️" if not it['done'] else "↩️"
            toggle_row.append(InlineKeyboardButton(f"{icon} {idx+1}", callback_data=f"todo_tog_{idx}"))
        rows.append(toggle_row)
        if len(tasks) > 5:
            toggle_row2 = []
            for idx, it in enumerate(tasks[5:10], start=5):
                icon = "☑️" if not it['done'] else "↩️"
                toggle_row2.append(InlineKeyboardButton(f"{icon} {idx+1}", callback_data=f"todo_tog_{idx}"))
            rows.append(toggle_row2)

    lbl_add = {'uz': "➕ Maqsad qoʻshish", 'tr': "➕ Hedef Ekle", 'ru': "➕ Добавить цель", 'en': "➕ Add Goal"}.get(lang, "➕ Add")
    lbl_clear = {'uz': "🗑️ Bajarilganlarni tozalash", 'tr': "🗑️ Tamamlananları Temizle", 'ru': "🗑️ Очистить выполненные", 'en': "🗑️ Clear Done"}.get(lang, "🗑️ Clear")
    lbl_back = {'uz': "🔙 Imtihonlarga qaytish", 'tr': "🔙 Sınavlara Dön", 'ru': "🔙 К экзаменам", 'en': "🔙 Back to Exams"}.get(lang, "🔙 Back")
    
    rows.append([
        InlineKeyboardButton(lbl_add, callback_data="todo_add"),
        InlineKeyboardButton(lbl_clear, callback_data="todo_clear")
    ])
    rows.append([InlineKeyboardButton(lbl_back, callback_data="exam_back_hub")])
    return InlineKeyboardMarkup(rows)

def get_prayer_hub_keyboard(user_id: int, lang: str = 'uz'):
    t = TEXTS.get(lang, TEXTS['uz'])
    rows = [
        [
            InlineKeyboardButton(t.get('btn_qaza_tracker', "📿 Qazo Daftari"), callback_data="open_qaza_tracker"),
            InlineKeyboardButton(t.get('btn_ramadan_countdown', "🌙 Saharlik & Iftor"), callback_data="open_ramadan_countdown")
        ],
        [
            InlineKeyboardButton(t['btn_adhkar_hub'], callback_data="open_adhkar_hub"),
            InlineKeyboardButton(t['btn_daily_hadith'], callback_data="open_hadith_0")
        ],
        [
            InlineKeyboardButton(t['btn_hijri_cal'], callback_data="open_hijri_cal"),
            InlineKeyboardButton(t['btn_imsakiye_pdf'], callback_data="gen_imsakiye_pdf")
        ],
        [
            InlineKeyboardButton(t['btn_kerahat_info'], callback_data="show_kerahat_info"),
            InlineKeyboardButton(t['btn_prayer_notif'], callback_data="open_prayer_notif_menu")
        ],
        [
            InlineKeyboardButton(t['btn_change_prayer_city'], callback_data="change_prayer_city")
        ]
    ]
    return InlineKeyboardMarkup(rows)

# =====================================================================
# KAZA VE RAMAZAN SAHUR/İFTAR GERİ SAYIM ŞABLONLARI
# =====================================================================
def format_qaza_card(user_id: int, lang: str = 'uz') -> str:
    q = get_user_qaza(user_id)
    t = TEXTS.get(lang, TEXTS['uz'])
    total = sum(q.values())

    labels = {
        'uz': {'fajr': "Bomdod", 'dhuhr': "Peshin", 'asr': "Asr", 'maghrib': "Shom", 'isha': "Xufton", 'witr': "Vitr (Vojib)"},
        'tr': {'fajr': "Sabah", 'dhuhr': "Öğle", 'asr': "İkindi", 'maghrib': "Akşam", 'isha': "Yatsı", 'witr': "Vitir (Vacip)"},
        'ru': {'fajr': "Фаджр", 'dhuhr': "Зухр", 'asr': "Аср", 'maghrib': "Магриб", 'isha': "Иша", 'witr': "Витр"},
        'en': {'fajr': "Fajr", 'dhuhr': "Dhuhr", 'asr': "Asr", 'maghrib': "Maghrib", 'isha': "Isha", 'witr': "Witr"},
    }.get(lang, labels['uz'])

    header = t.get('qaza_title', "*NUN PROJECT // QAZO NAMOZLARI DEFTERI*")
    total_lbl = t.get('qaza_total', "Jami qazo namozlari borchi:")

    lines = [
        header,
        "",
        f"▫️ *{labels['fajr']}:*    `{q['fajr']}` ta",
        f"▫️ *{labels['dhuhr']}:*    `{q['dhuhr']}` ta",
        f"▫️ *{labels['asr']}:*     `{q['asr']}` ta",
        f"▫️ *{labels['maghrib']}:*    `{q['maghrib']}` ta",
        f"▫️ *{labels['isha']}:*    `{q['isha']}` ta",
        f"▫️ *{labels['witr']}:*    `{q['witr']}` ta",
        "",
        f"📊 *{total_lbl}* `{total}` ta",
        "",
        "💡 _Quyidagi tugmalar orqali qazo namozlaringizni oshiring yoki kamaytiring:_"
    ]
    return "\n".join(lines)

def build_qaza_keyboard(user_id: int, lang: str = 'uz') -> InlineKeyboardMarkup:
    labels = {
        'uz': {'fajr': "Bomdod", 'dhuhr': "Peshin", 'asr': "Asr", 'maghrib': "Shom", 'isha': "Xufton", 'witr': "Vitr"},
        'tr': {'fajr': "Sabah", 'dhuhr': "Öğle", 'asr': "İkindi", 'maghrib': "Akşam", 'isha': "Yatsı", 'witr': "Vitir"},
        'ru': {'fajr': "Фаджр", 'dhuhr': "Зухр", 'asr': "Аср", 'maghrib': "Магриб", 'isha': "Иша", 'witr': "Витр"},
        'en': {'fajr': "Fajr", 'dhuhr': "Dhuhr", 'asr': "Asr", 'maghrib': "Maghrib", 'isha': "Isha", 'witr': "Witr"},
    }.get(lang, labels['uz'])

    btns = [
        [
            InlineKeyboardButton(f"➕ {labels['fajr']}", callback_data="qaza_add_fajr"),
            InlineKeyboardButton("➖ 1", callback_data="qaza_sub_fajr"),
            InlineKeyboardButton(f"➕ {labels['dhuhr']}", callback_data="qaza_add_dhuhr"),
            InlineKeyboardButton("➖ 1", callback_data="qaza_sub_dhuhr")
        ],
        [
            InlineKeyboardButton(f"➕ {labels['asr']}", callback_data="qaza_add_asr"),
            InlineKeyboardButton("➖ 1", callback_data="qaza_sub_asr"),
            InlineKeyboardButton(f"➕ {labels['maghrib']}", callback_data="qaza_add_maghrib"),
            InlineKeyboardButton("➖ 1", callback_data="qaza_sub_maghrib")
        ],
        [
            InlineKeyboardButton(f"➕ {labels['isha']}", callback_data="qaza_add_isha"),
            InlineKeyboardButton("➖ 1", callback_data="qaza_sub_isha"),
            InlineKeyboardButton(f"➕ {labels['witr']}", callback_data="qaza_add_witr"),
            InlineKeyboardButton("➖ 1", callback_data="qaza_sub_witr")
        ],
        [
            InlineKeyboardButton("🔄 Barchasini Qayta Oʻrnatish (Sıfırla)", callback_data="qaza_reset_prompt")
        ],
        [
            InlineKeyboardButton("🔙 Namoz Markaziga Qaytish", callback_data="qaza_back_prayer")
        ]
    ]
    return InlineKeyboardMarkup(btns)

def format_ramadan_countdown_card(user_id: int, lang: str = 'uz', user_now: datetime = None) -> str:
    if not user_now:
        user_now = get_user_now(user_id)
    today_date = user_now.date()
    today_str = today_date.strftime("%Y-%m-%d")

    u_coords = get_user_coords(user_id)
    saved_city = get_user_city(user_id) or "Shahar"
    tz_off = get_user_tz_offset(user_id)

    timings = None
    if u_coords:
        c_key = f"{user_id}_{today_str}"
        if c_key in DAILY_PRAYER_CACHE:
            timings = DAILY_PRAYER_CACHE[c_key]
        else:
            calc = AstroPrayerTimes(u_coords['lat'], u_coords['lon'], elevation=u_coords.get('elevation', 0.0), tz_offset=float(tz_off), country_code=u_coords.get('country', ''))
            timings = calc.calculate_times(user_now)
            DAILY_PRAYER_CACHE[c_key] = timings

    if not timings:
        timings = {"Fajr": "05:00", "Sunrise": "06:30", "Dhuhr": "12:30", "Asr": "16:15", "Maghrib": "18:45", "Isha": "20:00"}

    def parse_p_dt(hm_str, add_days=0):
        parts = hm_str.split(":")
        d = today_date + timedelta(days=add_days)
        return datetime(d.year, d.month, d.day, int(parts[0]), int(parts[1][:2]))

    fajr_dt = parse_p_dt(timings['Fajr'])
    if fajr_dt < user_now:
        fajr_dt = parse_p_dt(timings['Fajr'], add_days=1)

    maghrib_dt = parse_p_dt(timings['Maghrib'])
    if maghrib_dt < user_now:
        maghrib_dt = parse_p_dt(timings['Maghrib'], add_days=1)

    diff_fajr = fajr_dt - user_now
    diff_maghrib = maghrib_dt - user_now

    def fmt_diff(td):
        s = int(td.total_seconds())
        return s // 3600, (s % 3600) // 60

    fh, fm = fmt_diff(diff_fajr)
    mh, mm = fmt_diff(diff_maghrib)

    titles = {
        'uz': ("*NUN PROJECT // SAHARLIK VA IFTORLIK SAYOGʻI*", "Saharlik tugashiga (Bomdod)", "Iftorlikka (Shom)", "soat", "daq qoldi"),
        'tr': ("*NUN PROJECT // SAHUR VE İFTAR CANLI SAYACI*", "Sahur bitimine (İmsak)", "İftara (Akşam Ezanı)", "saat", "dk kaldı"),
        'ru': ("*NUN PROJECT // СЧЕТЧИК СУХУРА И ИФТАРА*", "До окончания сухура (Фаджр)", "До ифтара (Магриб)", "ч.", "мин. осталось"),
        'en': ("*NUN PROJECT // SUHOOR & IFTAR COUNTDOWN*", "Until Suhoor ends (Fajr)", "Until Iftar (Maghrib)", "hrs", "mins left"),
    }
    h_title, lbl_suhoor, lbl_iftar, u_h, u_m = titles.get(lang, titles['uz'])

    return (
        f"{h_title}\n"
        f"📍 *[ {saved_city.upper()} ]* | 📅 `{user_now.strftime('%d.%m.%Y')}`\n\n"
        f"┌──────────────────────────────┐\n"
        f"  🍲 *{lbl_suhoor}:*\n"
        f"     ⏰ `{timings['Fajr']}` ➔ ⏳ `{fh} {u_h} {fm} {u_m}`\n\n"
        f"  ✨ *{lbl_iftar}:*\n"
        f"     ⏰ `{timings['Maghrib']}` ➔ ⏳ `{mh} {u_h} {mm} {u_m}`\n"
        f"└──────────────────────────────┘\n\n"
        f"🤲 _«Oʻz vaqtida ochilgan iftor va niyat bilan qilingan ibodat maqbuldir.»_"
    )

# =====================================================================
# HAVA DURUMU MOTORU (OPEN-METEO GLOBAL SERVICE)
# =====================================================================
WMO_WEATHER_CODES = {
    0: {'uz': ("☀️", "Musaffo ochiq osmon"), 'tr': ("☀️", "Açık ve güneşli"), 'ru': ("☀️", "Ясно"), 'en': ("☀️", "Clear sky")},
    1: {'uz': ("🌤️", "Asosan ochiq"), 'tr': ("🌤️", "Az bulutlu"), 'ru': ("🌤️", "Преимущественно ясно"), 'en': ("🌤️", "Mainly clear")},
    2: {'uz': ("⛅", "Qisman bulutli"), 'tr': ("⛅", "Parçalı bulutlu"), 'ru': ("⛅", "Переменная облачность"), 'en': ("⛅", "Partly cloudy")},
    3: {'uz': ("☁️", "Bulutli"), 'tr': ("☁️", "Çok bulutlu / Kapalı"), 'ru': ("☁️", "Пасмурно"), 'en': ("☁️", "Overcast")},
    45: {'uz': ("🌫️", "Tuman"), 'tr': ("🌫️", "Sisli"), 'ru': ("🌫️", "Туман"), 'en': ("🌫️", "Fog")},
    48: {'uz': ("🌫️", "Qirovli tuman"), 'tr': ("🌫️", "Kırağılı sis"), 'ru': ("🌫️", "Изморозь"), 'en': ("🌫️", "Depositing rime fog")},
    51: {'uz': ("🌦️", "Yengil mayda yomgʻir"), 'tr': ("🌦️", "Hafif çiseleme"), 'ru': ("🌦️", "Легкая морось"), 'en': ("🌦️", "Light drizzle")},
    53: {'uz': ("🌦️", "Oʻrtacha mayda yomgʻir"), 'tr': ("🌦️", "Çiseleme"), 'ru': ("🌦️", "Умеренная морось"), 'en': ("🌦️", "Moderate drizzle")},
    55: {'uz': ("🌧️", "Kuchli mayda yomgʻir"), 'tr': ("🌧️", "Yoğun çiseleme"), 'ru': ("🌧️", "Густая морось"), 'en': ("🌧️", "Dense drizzle")},
    61: {'uz': ("🌧️", "Yengil yomgʻir"), 'tr': ("🌧️", "Hafif yağmurlu"), 'ru': ("🌧️", "Небольшой дождь"), 'en': ("🌧️", "Slight rain")},
    63: {'uz': ("🌧️", "Oʻrtacha yomgʻir"), 'tr': ("🌧️", "Yağmurlu"), 'ru': ("🌧️", "Умеренный дождь"), 'en': ("🌧️", "Moderate rain")},
    65: {'uz': ("🌧️", "Kuchli jala yomgʻir"), 'tr': ("🌧️", "Kuvvetli yağmur"), 'ru': ("🌧️", "Сильный дождь"), 'en': ("🌧️", "Heavy rain")},
    71: {'uz': ("🌨️", "Yengil qor"), 'tr': ("🌨️", "Hafif kar yağışlı"), 'ru': ("🌨️", "Небольшой снегопад"), 'en': ("🌨️", "Slight snowfall")},
    73: {'uz': ("🌨️", "Oʻrtacha qor"), 'tr': ("🌨️", "Kar yağışlı"), 'ru': ("🌨️", "Умеренный снегопад"), 'en': ("🌨️", "Moderate snowfall")},
    75: {'uz': ("❄️", "Kuchli qor boʻroni"), 'tr': ("❄️", "Yoğun kar yağışı"), 'ru': ("❄️", "Сильный снегопад"), 'en': ("❄️", "Heavy snowfall")},
    80: {'uz': ("🌧️", "Qisqa muddatli yomgʻir"), 'tr': ("🌧️", "Hafif sağanak"), 'ru': ("🌧️", "Кратковременный дождь"), 'en': ("🌧️", "Slight showers")},
    81: {'uz': ("🌧️", "Oʻrtacha jala"), 'tr': ("🌧️", "Sağanak yağış"), 'ru': ("🌧️", "Ливень"), 'en': ("🌧️", "Moderate showers")},
    82: {'uz': ("⛈️", "Kuchli jala"), 'tr': ("⛈️", "Kuvvetli sağanak"), 'ru': ("⛈️", "Сильный ливень"), 'en': ("⛈️", "Violent showers")},
    95: {'uz': ("⛈️", "Momaqaldiroq"), 'tr': ("⛈️", "Gök gürültülü fırtına"), 'ru': ("⛈️", "Гроза"), 'en': ("⛈️", "Thunderstorm")},
    96: {'uz': ("⛈️", "Doʻlli momaqaldiroq"), 'tr': ("⛈️", "Dolulu gök gürültülü fırtına"), 'ru': ("⛈️", "Гроза с градом"), 'en': ("⛈️", "Thunderstorm with hail")},
}

def get_weather_desc(code: int, lang: str = 'uz'):
    entry = WMO_WEATHER_CODES.get(code, WMO_WEATHER_CODES.get(0))
    return entry.get(lang, entry['uz'])

async def fetch_weather(city_query: str, lang: str = 'uz'):
    clean_q = re.sub(r"['’`ʻʼ]", "", city_query.strip())
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NunBot-Weather/3.0"}
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(clean_q)}&count=1&language=en&format=json"
    
    try:
        client = get_http_client()
        resp = await client.get(geo_url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results")
            if results and len(results) > 0:
                best = results[0]
                lat = best["latitude"]
                lon = best["longitude"]
                name = best.get("name", city_query.title())
                admin1 = best.get("admin1", "")
                country = best.get("country", "")
                    
                forecast_url = (
                    f"https://api.open-meteo.com/v1/forecast?"
                    f"latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
                    f"&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=auto"
                )
                w_resp = await client.get(forecast_url, headers=headers)
                if w_resp.status_code == 200:
                    w_data = w_resp.json()
                    cur = w_data.get("current", {})
                    daily = w_data.get("daily", {})
                        
                    cur_temp = cur.get("temperature_2m", 0.0)
                    feels_like = cur.get("apparent_temperature", cur_temp)
                    humidity = cur.get("relative_humidity_2m", 0)
                    wind_speed = cur.get("wind_speed_10m", 0.0)
                    w_code = cur.get("weather_code", 0)
                        
                    t_min = daily.get("temperature_2m_min", [cur_temp])[0] if daily.get("temperature_2m_min") else cur_temp
                    t_max = daily.get("temperature_2m_max", [cur_temp])[0] if daily.get("temperature_2m_max") else cur_temp
                        
                    return name, admin1, country, cur_temp, feels_like, humidity, wind_speed, w_code, t_min, t_max
    except Exception as e:
        print(f"[WEATHER_GEO_ERROR] {e}")

    try:
        client = get_http_client()
        resp = await client.get(f"https://wttr.in/{urllib.parse.quote(city_query)}?format=j1", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            cur_cond = data.get("current_condition", [{}])[0]
            area = data.get("nearest_area", [{}])[0]
            name = area.get("areaName", [{}])[0].get("value", city_query.title())
            country = area.get("country", [{}])[0].get("value", "")
            admin1 = area.get("region", [{}])[0].get("value", "")
                
            cur_temp = float(cur_cond.get("temp_C", 0.0))
            feels_like = float(cur_cond.get("FeelsLikeC", cur_temp))
            humidity = int(cur_cond.get("humidity", 0))
            wind_speed = float(cur_cond.get("windspeedKmph", 0.0))
                
            weather = data.get("weather", [{}])[0]
            t_min = float(weather.get("mintempC", cur_temp))
            t_max = float(weather.get("maxtempC", cur_temp))
                
            return name, admin1, country, cur_temp, feels_like, humidity, wind_speed, 1, t_min, t_max
    except Exception as e:
        print(f"[WEATHER_WTTR_FALLBACK_ERROR] {e}")
        
    return None, None, None, None, None, None, None, None, None, None

def format_weather_card(city_name: str, admin_name: str, country: str, cur_temp: float, feels_like: float, humidity: int, wind_speed: float, w_code: int, t_min: float, t_max: float, lang: str = 'uz') -> str:
    emoji, desc = get_weather_desc(w_code, lang)
    loc_parts = [p for p in [city_name, admin_name, country] if p]
    full_loc = ", ".join(loc_parts)

    headers = {
        'uz': "*NUN PROJECT // OB-HAVO MAʼLUMOTI*",
        'tr': "*NUN PROJECT // HAVA DURUMU*",
        'ru': "*NUN PROJECT // ПРОГНОЗ ПОГОДЫ*",
        'en': "*NUN PROJECT // WEATHER FORECAST*",
    }
    lbls = {
        'uz': ("Holat", "Harorat", "His qilinishi", "Namlik", "Shamol tezligi", "Kungi min / maks", "km/soat"),
        'tr': ("Durum", "Sıcaklık", "Hissedilen", "Nem", "Rüzgar Hızı", "Günün En Düşük / Yüksek", "km/saat"),
        'ru': ("Состояние", "Температура", "Ощущается как", "Влажность", "Скорость ветра", "Мин / Макс за день", "км/ч"),
        'en': ("Condition", "Temperature", "Feels Like", "Humidity", "Wind Speed", "Daily Min / Max", "km/h"),
    }
    hdr = headers.get(lang, headers['uz'])
    l = lbls.get(lang, lbls['uz'])

    return (
        f"{hdr}\n"
        f"📍 *[ {full_loc.upper()} ]*\n\n"
        f"┌────────────────────────────┐\n"
        f"  ▫️ *{l[0]}:*  {emoji} `{desc}`\n"
        f"  ▫️ *{l[1]}:*  `{cur_temp:+.1f}°C`\n"
        f"  ▫️ *{l[2]}:*  `{feels_like:+.1f}°C`\n"
        f"  ▫️ *{l[3]}:*  `{humidity}%`\n"
        f"  ▫️ *{l[4]}:*  `{wind_speed:.1f} {l[6]}`\n"
        f"  ▫️ *{l[5]}:*  `{t_min:+.1f}°C / {t_max:+.1f}°C`\n"
        f"└────────────────────────────┘\n"
        f"_Open-Meteo Global Weather Service_"
    )

# =====================================================================
# ÇOK DİLLİ ZAMANLAYICI PANELİ & TAKVİM
# =====================================================================
def format_scheduler_card(title: str, dt: datetime, lang: str = 'uz') -> str:
    month_names = {
        'uz': ["", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentyabr", "Oktyabr", "Noyabr", "Dekabr"],
        'tr': ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"],
        'ru': ["", "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"],
        'en': ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    }
    m_name = month_names.get(lang, month_names['uz'])[dt.month]
    clean_title = re.sub(r"[*_`\[\]]", "", title)

    if lang == 'tr':
        header = "*NUN PROJECT // ZAMAN AYARI*"
        lbl_exam, lbl_date, lbl_time = "Ders / Sınav", "Tarih", "Saat"
        hint = "_Aşağıdaki butonlarla tarihi ve saati canlı olarak ayarlayın:_"
    elif lang == 'ru':
        header = "*NUN PROJECT // НАСТРОЙКА ВРЕМЕНИ*"
        lbl_exam, lbl_date, lbl_time = "Предмет / Экзамен", "Дата", "Время"
        hint = "_Настройте дату и время с помощью кнопок ниже:_"
    elif lang == 'en':
        header = "*NUN PROJECT // SCHEDULE TIME*"
        lbl_exam, lbl_date, lbl_time = "Subject / Exam", "Date", "Time"
        hint = "_Adjust date and time in real-time using buttons below:_"
    else:
        header = "*NUN PROJECT // VAQTNI SOZLASH*"
        lbl_exam, lbl_date, lbl_time = "Fan / Imtihon", "Sana", "Vaqt"
        hint = "_Quyidagi tugmalar orqali sana va vaqtni jonli sozlang:_"

    return (
        f"{header}\n"
        f"📌 {lbl_exam}: *{safe_md(clean_title)}*\n\n"
        f"┌──────────────────────────────┐\n"
        f"  🗓 {lbl_date}:   `{dt.day} {m_name} {dt.year}`\n"
        f"  ⏰ {lbl_time}:   `{dt.strftime('%H : %M')}`\n"
        f"└──────────────────────────────┘\n"
        f"{hint}"
    )

def build_scheduler_keyboard(lang: str = 'uz') -> InlineKeyboardMarkup:
    labels = {
        'uz': {
            'day_prev': "◀️ Kun", 'day_next': "Kun ▶️",
            'hour_minus': "➖ 1 soat", 'hour_plus': "➕ 1 soat",
            'min_minus': "➖ 15 daq", 'min_plus': "➕ 15 daq",
            'open_cal': "🗓 Taqvimdan tanlash",
            'today': "⚡ Bugun", 'tmrw': "⚡ Ertaga", 'week': "⚡ 1 hafta",
            'confirm': "✅ TASDIQLASH VA SAQLASH",
            'cancel': "❌ Bekor qilish"
        },
        'tr': {
            'day_prev': "◀️ Gün", 'day_next': "Gün ▶️",
            'hour_minus': "➖ 1 Saat", 'hour_plus': "➕ 1 Saat",
            'min_minus': "➖ 15 Dk", 'min_plus': "➕ 15 Dk",
            'open_cal': "🗓 Takvimden Seç",
            'today': "⚡ Bugün", 'tmrw': "⚡ Yarın", 'week': "⚡ 1 Hafta",
            'confirm': "✅ ONAYLA VE KAYDET",
            'cancel': "❌ İptal"
        },
        'ru': {
            'day_prev': "◀️ День", 'day_next': "День ▶️",
            'hour_minus': "➖ 1 час", 'hour_plus': "➕ 1 час",
            'min_minus': "➖ 15 мин", 'min_plus': "➕ 15 мин",
            'open_cal': "🗓 Выбрать из календаря",
            'today': "⚡ Сегодня", 'tmrw': "⚡ Завтра", 'week': "⚡ 1 неделя",
            'confirm': "✅ ПОДТВЕРДИТЬ И СОХРАНИТЬ",
            'cancel': "❌ Отмена"
        },
        'en': {
            'day_prev': "◀️ Day", 'day_next': "Day ▶️",
            'hour_minus': "➖ 1 hr", 'hour_plus': "➕ 1 hr",
            'min_minus': "➖ 15 min", 'min_plus': "➕ 15 min",
            'open_cal': "🗓 Select from Calendar",
            'today': "⚡ Today", 'tmrw': "⚡ Tomorrow", 'week': "⚡ 1 week",
            'confirm': "✅ CONFIRM & SAVE",
            'cancel': "❌ Cancel"
        }
    }
    lbl = labels.get(lang, labels['uz'])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl['day_prev'], callback_data="sched_day_prev"), InlineKeyboardButton(lbl['day_next'], callback_data="sched_day_next")],
        [InlineKeyboardButton(lbl['hour_minus'], callback_data="sched_hour_minus"), InlineKeyboardButton(lbl['hour_plus'], callback_data="sched_hour_plus")],
        [InlineKeyboardButton(lbl['min_minus'], callback_data="sched_min_minus"), InlineKeyboardButton(lbl['min_plus'], callback_data="sched_min_plus")],
        [InlineKeyboardButton(lbl['today'], callback_data="sched_jump_today"), InlineKeyboardButton(lbl['tmrw'], callback_data="sched_jump_tmrw"), InlineKeyboardButton(lbl['week'], callback_data="sched_jump_week")],
        [InlineKeyboardButton(lbl['open_cal'], callback_data="sched_open_cal")],
        [InlineKeyboardButton(lbl['confirm'], callback_data="sched_confirm")],
        [InlineKeyboardButton(lbl['cancel'], callback_data="cancel_action")],
    ])

def build_month_calendar(year: int, month: int, lang: str = 'uz') -> InlineKeyboardMarkup:
    month_names = {
        'uz': ["", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentyabr", "Oktyabr", "Noyabr", "Dekabr"],
        'tr': ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"],
        'ru': ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"],
        'en': ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    }
    week_days = {
        'uz': ["Du", "Se", "Cho", "Pa", "Ju", "Sha", "Ya"],
        'tr': ["Pt", "Sa", "Ça", "Pe", "Cu", "Ct", "Pz"],
        'ru': ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
        'en': ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
    }
    back_labels = {'uz': "🔙 Orqaga", 'tr': "🔙 Geri Dön", 'ru': "🔙 Назад", 'en': "🔙 Back"}

    m_names = month_names.get(lang, month_names['uz'])
    w_days = week_days.get(lang, week_days['uz'])
    btn_back = back_labels.get(lang, back_labels['uz'])

    rows = [
        [
            InlineKeyboardButton("◀️", callback_data=f"cal_nav_{year if month > 1 else year - 1}_{month - 1 if month > 1 else 12}"),
            InlineKeyboardButton(f"{m_names[month]} {year}", callback_data="cal_ignore"),
            InlineKeyboardButton("▶️", callback_data=f"cal_nav_{year if month < 12 else year + 1}_{month + 1 if month < 12 else 1}"),
        ],
        [InlineKeyboardButton(d, callback_data="cal_ignore") for d in w_days]
    ]
    cal = calendar.monthcalendar(year, month)
    today = datetime.now()
    for week in cal:
        r = []
        for d in week:
            if d == 0:
                r.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
            else:
                label = f"•{d}•" if (today.year == year and today.month == month and today.day == d) else str(d)
                r.append(InlineKeyboardButton(label, callback_data=f"cal_pick_{year:04d}-{month:02d}-{d:02d}"))
        rows.append(r)
    rows.append([InlineKeyboardButton(btn_back, callback_data="cal_back_panel")])
    return InlineKeyboardMarkup(rows)

async def safe_edit_text_markup(message, text: str, reply_markup=None, parse_mode=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            clean = re.sub(r'<[^>]+>', '', text).replace("*", "").replace("_", "").replace("`", "")
            await message.edit_text(clean, reply_markup=reply_markup)
        except Exception:
            pass

def get_pdf_hub_keyboard(lang: str = 'uz'):
    t = TEXTS.get(lang, TEXTS['uz'])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t['pdf_hub_to_pdf_btn'], callback_data="pdf_act_to_pdf")],
        [InlineKeyboardButton(t['pdf_hub_ocr_btn'], callback_data="pdf_act_ocr")],
    ])

def get_pomodoro_keyboard(user_id: int, lang: str = 'uz'):
    t = TEXTS.get(lang, TEXTS['uz'])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t['pomo_btn_work25'], callback_data="pomo_25"), InlineKeyboardButton(t['pomo_btn_break5'], callback_data="pomo_5")],
        [InlineKeyboardButton(t['pomo_btn_work50'], callback_data="pomo_50"), InlineKeyboardButton(t['pomo_btn_break10'], callback_data="pomo_10")],
        [InlineKeyboardButton(t['pomo_btn_add_remind'], callback_data="remind_add"), InlineKeyboardButton(t['pomo_btn_my_reminds'], callback_data="remind_list")],
    ])

def get_adhkar_selection_keyboard(lang: str = 'uz'):
    t = TEXTS.get(lang, TEXTS['uz'])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t['adhkar_morning_btn'], callback_data="adhkar_morning"), 
         InlineKeyboardButton(t['adhkar_evening_btn'], callback_data="adhkar_evening")],
        [InlineKeyboardButton(t['adhkar_salawat_btn'], callback_data="adhkar_salawat")]
    ])

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 Oʻzbekcha", callback_data="lang_uz"), InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")],
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ])

def get_timezone_keyboard(lang: str = 'uz'):
    t = TEXTS.get(lang, TEXTS['uz'])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 Toshkent (UTC+5)", callback_data="settz_5"), InlineKeyboardButton("🇹🇷 Istanbul (UTC+3)", callback_data="settz_3")],
        [InlineKeyboardButton("🇷🇺 Moskva (UTC+3)", callback_data="settz_3_ru"), InlineKeyboardButton("🇦🇪 Dubay (UTC+4)", callback_data="settz_4")],
        [InlineKeyboardButton("🇬🇧 London (UTC+0)", callback_data="settz_0"), InlineKeyboardButton("🇩🇪 Berlin (UTC+1)", callback_data="settz_1")],
        [InlineKeyboardButton("🇰🇿 Olmaota (UTC+5)", callback_data="settz_5_kz"), InlineKeyboardButton("🇺🇸 New York (UTC-5)", callback_data="settz_-5")],
        [InlineKeyboardButton(t['btn_auto_loc'], callback_data="tz_req_location")],
        [InlineKeyboardButton(t['btn_cancel'], callback_data="cancel_action")]
    ])

# =====================================================================
# 4 DİLLİ TAM VE EKSİKSİZ SÖZLÜK (KUSURSUZ MEALLER & İMLA)
# =====================================================================
TEXTS = {
    'uz': {
        'btn_qaza_tracker': '📿 Qazo Daftari',
        'qaza_title': '*NUN PROJECT // QAZO NAMOZLARI DEFTERI*',
        'qaza_total': 'Jami qazo namozlari borchi:',
        'btn_ramadan_countdown': '🌙 Saharlik & Iftorlik Sayogʻi',
        'flood_jail_alert': '⚠️ Siz juda koʻp qisqa vaqt ichida soʻrov yubordingiz. Tizim xavfsizligi uchun 10 daqiqa kuting.',
        'btn_download_audio': '🎵 Faqat audio (MP3) yuklash',
        'audio_downloading': '🎵 Audio chiqarilmoqda, kuting...',
        'audio_ready': 'Audio muvaffaqiyatli tayyorlandi!',
        'btn_feedback': "💡 Fikr & Taklif",
        'prompt_feedback': "💡 *FIKR VA TAKLIFLAR*\n\nNun Bot haqidagi taklif, mulohaza yoki xatolik haqida yozib yuboring. Xabaringiz toʻgʻridan-toʻgʻri maʼmuriyatga yetkaziladi:\n\n_(Bekor qilish uchun /cancel)_",
        'feedback_sent': "✅ Fikr-mulohazangiz maʼmuriyatga yetkazildi. Rahmat!",
        'maintenance_msg': "🚧 *TEXNIK XIZMAT KOʻRSATILMOQDA*\n\nBotda yangilanish va optimallashtirish ishlari olib borilmoqda. Iltimos, birozdan soʻng qayta urinib koʻring.",
        'banned_msg': "⛔ Sizning ushbu botdan foydalanishingiz maʼmuriyat tomonidan cheklangan.",
        'welcome': "Assalomu alaykum! Nun Botga xush kelibsiz.\nQuyidagi menyudan kerakli boʻlimni tanlang:",
        'menu_title': "📋 Asosiy menyu:",
        'btn_video': "🎬 Video yuklash",
        'btn_prayer': "🕌 Namoz & Ibodat",
        'btn_adhkar_hub': "📿 Zikrlar & Salovatlar",
        'btn_daily_hadith': "📖 Kunning hadisi",
        'btn_exam_todo': "📝 Kunlik vazifalar (To-Do)",
        'prompt_todo_add': "✍️ Bajarmoqchi boʻlgan yangi dars maqsadingizni yozib yuboring:\n_(Masalan: Matematika 20 ta masala, Fizika laboratoriya tayyorlash)_ ",
        'btn_imsakiye_pdf': "📄 30 kunlik Taqvim (PDF)",
        'btn_kerahat_info': "⚠️ Karohiyat & Ishroq",
        'btn_hijri_cal': "🌙 Diniy kunlar taqvimi",
        'btn_weather': "🌤️ Ob-havo",
        'btn_pdf_hub': "📄 PDF & Hujjatlar",
        'btn_exam': "🎓 Imtihon & Taymer",
        'btn_schedule_img': "🗓️ Dars jadvali",
        'btn_pomodoro': "⏱️ Pomodoro & Eslatma",
        'btn_translit': "🔤 Kirill ⇄ Lotin",
        'btn_adhkar': "📿 Zikrlar & Salovatlar",
        'btn_timezone_hub': "🕒 Vaqt & Joylashuv",
        'btn_lang': "🌐 Tilni tanlash",
        'prayer_loading': "⏳ Namoz vaqtlari hisoblanmoqda...",
        'btn_reset': "🔄 Qayta boshlash & Tozalash",
        'reset_success': "🔄 *Bot qayta ishga tushirildi va chat tozalandi!*\n\nSana, soat va joylashuvingiz saqlab qolindi. Barcha dars maqsadlari, imtihonlar, eslatmalar va eski xabarlar tozalandi.\n\n_Nun Bot xizmatingizda:_ ",
        'btn_timezone': "🕒 Vaqt mintaqasi",
        'btn_city_label': "Shahar",
        'btn_auto_loc': "📍 Avtomatik aniqlash (Joylashuv / Shahar)",
        'btn_change_prayer_city': "🔄 Shaharni oʻzgartirish",
        'btn_change_weather_city': "🔄 Boshqa shahar ob-havosi",
        'prompt_video': "🔗 Instagram, TikTok, Facebook, X (Twitter) yoki YouTube havolasini yuboring:",
        'prompt_prayer': "🕌 *NUN PROJECT // NAMOZ VAQTLARI*\n\nNamoz vaqtlarini bilmoqchi boʻlgan shahar nomini yozib yuboring:\n_(Masalan: *Qoʻqon*, *Toshkent*, *Samarqand*, *Istanbul*...)_",
        'prompt_weather': "🌤️ *NUN PROJECT // OB-HAVO XIZMATI*\n\nOb-havo maʼlumotini bilmoqchi boʻlgan shahar, tuman yoki qishloq nomini yozib yuboring:\n_(Masalan: *Qoʻqon*, *Rishton*, *Toshkent*, *Istanbul*, *Moskva*...)_",
        'prompt_pdf_hub': "📄 *NUN PROJECT // PDF & HUJJATLAR MARKAZI*\n\nAmalni tanlang:",
        'prompt_schedule_img': "🗓️ *DARS JADVALI RASMI*\n\nDars jadvalingizni kunlar boʻyicha yozib yuboring (Masalan: Dushanba: 09:00 Matematika...):\nBot uni 1080x1920 qulflangan ekran formatiga aylantiradi.",
        'prompt_pomodoro': "⏱️ *POMODORO & ESLATMA MARKAZI*",
        'prompt_adhkar': "📿 Zikr yoki Salovat turini tanlang:",
        'prompt_translit': "✍️ Matningizni yuboring, avtomatik Kirill ⇄ Lotin oʻgirib beraman:",
        'prompt_convert_to_pdf': "📸 *PDF GA OʻGIRISH REJIMI FAOL*\n\nPDF formatiga oʻtkazmoqchi boʻlgan faylni yuboring:\n_(Rasm, Word .docx, Excel .xlsx yoki TXT)_",
        'prompt_ocr': "🔍 *RASMDAN MATN CHIQARISH (OCR) FAOL*\n\nMatnini oʻqib olmoqchi boʻlgan kitob yoki taxta rasmini yuboring:\n_(Arabcha, Xitoycha, Ruscha, Oʻzbekcha va barcha tillar qoʻllab-quvvatlanadi)_",
        'prompt_exam_title': "🎓 *IMTIHON QOʻSHISH*\n\n✍️ Imtihon yoki fanning nomini yozib yuboring:\n_(Masalan: *Oliy Matematika*, *Fizika Final*)_",
        'prompt_remind': "⏰ Eslatmani quyidagi formatda yuboring:\n`Kitob o'qish - 18:30` yoki `Dars - 30 daqiqa`",
        'prompt_timezone': "🕒 *VAQT MINTAQASI VA JOYLASHUV*",
        'prompt_send_location': "📍 *JOY LASHUV / SHAHARNI YUBORING*\n\nIltimos, Telegram orqali joylashuvingizni (Location) yuboring yoki shahar nomini yozing (Masalan: *Toshkent*, *Istanbul*, *Moskva*, *London*...):",
        'prompt_city_sync_title': "SHAHAR YOKI JOYLASHUVNI BELGILANG",
        'prompt_city_sync_desc': "Namoz vaqtlari, taymer va eslatmalar toʻliq sizning mahalliy vaqtingizga koʻra ishlashi uchun hozir qaysi shahardasiz?\n_(Shahar nomini yozing, masalan: *Toshkent*, *Samarqand*, *Istanbul* yoki Location yuboring)_",
        'tz_hub_instruction': "Quyidagi tugmalardan shahar/mintaqani tanlang yoki yangi shahar nomini yozib yuboring:",
        'tz_prayer_synced_lbl': "namoz vaqtlari bilan sinxronlandi!",
        'tz_loc_detected': "JOYLASHUV VA VAQT ANIQLANDI",
        'tz_synced_hint': "Barcha taymerlar, eslatmalar va namoz vaqtlari sizning mahalliy vaqtingizga toʻliq moslashtirildi.",
        'current_time_lbl': "Joriy vaqtingiz",
        'pomo_started': "POMODORO BOSHLANDI",
        'pomo_work_label': "Dars",
        'pomo_break_label': "Dam olish",
        'pomo_mins_unit': "daq",
        'pomo_mode_lbl': "Rejim",
        'pomo_dur_lbl': "Davomiyligi",
        'pomo_end_lbl': "Tugash vaqti",
        'pomo_break_over': "TANAFFUS TUGADI!",
        'pomo_work_over': "POMODORO TUGADI!",
        'pomo_btn_work25': "🍅 25 daq dars",
        'pomo_btn_break5': "☕ 5 daq tanaffus",
        'pomo_btn_work50': "🍅 50 daq dars",
        'pomo_btn_break10': "☕ 10 daq tanaffus",
        'pomo_btn_add_remind': "⏰ Yangi Eslatma Qoʻshish",
        'pomo_btn_my_reminds': "📋 Eslatmalarim",
        'exam_empty': "Sizda hali saqlangan imtihon yoʻq.",
        'exam_btn_add': "➕ Imtihon qoʻshish",
        'exam_saved': "Imtihon muvaffaqiyatli saqlandi!",
        'exam_deleted': "Imtihon muvaffaqiyatli oʻchirildi.",
        'exam_default_title': "Imtihon",
        'exam_hub_title': "🎓 *NUN PROJECT // IMTIHONLAR TAYMERI*",
        'remind_saved': "Eslatma oʻrnatildi!",
        'remind_empty': "Sizda faol eslatmalar yoʻq.",
        'remind_due': "NUN PROJECT // ESLATMA",
        'remind_deleted': "Eslatma muvaffaqiyatli oʻchirildi.",
        'remind_task_lbl': "Vazifa",
        'remind_note': "Belgilangan vaqt yetib keldi!",
        'pdf_ready': "PDF hujjati muvaffaqiyatli tayyorlandi!",
        'pdf_fail': "Faylni PDF ga oʻgirishda xatolik yuz berdi.",
        'pdf_hub_to_pdf_btn': "📸 Rasm / Word / Excel / TXT ➔ PDF",
        'pdf_hub_ocr_btn': "🔍 Rasmdan Matn Olish (OCR)",
        'ocr_title': "RASMDAN OʻQIB OLINGAN MATN:",
        'ocr_fail': "Rasmdan tushunarli matn topilmadi.",
        'direct_img_prompt': "Rasm qabul qilindi. Qaysi amalni bajarmoqchisiz?",
        'btn_direct_pdf': "PDF ga aylantirish",
        'btn_direct_ocr': "Matnni oʻqish (OCR)",
        'btn_cancel': "Bekor qilish",
        'cancel_success': "Amal bekor qilindi.",
        'lang_changed': "Til muvaffaqiyatli oʻzgartirildi!",
        'tz_changed': "Vaqt mintaqasi muvaffaqiyatli saqlandi!",
        'city_not_found': "Shahar topilmadi. Shahar nomini toʻgʻri yozing.",
        'weather_loading': "🌤️ Ob-havo maʼlumoti olinmoqda...",
        'weather_city_not_found': "Aholi punkti topilmadi. Iltimos, shahar yoki tuman nomini toʻgʻri kiriting.",
        'loc_prompt_multimatch': "📍 *BIR NECHTA HUDUD TOPILDI*\n\nIltimos, sizga kerakli boʻlgan hududni tanlang:",
        'btn_prayer_notif': "🔔 Ezon & Vaqt bildirishnomasi",
        'notif_menu_title': "🔔 *NAMOZ VAQTI BILDIRISHNOMALARI*",
        'notif_menu_desc': "Namoz vaqtlari kirganida bot sizga avtomatik eslatma yuborsinmi?\nKerakli rejimni tanlang:",
        'notif_btn_on_time': "✅ Aynan vaqtida (Ezon)",
        'notif_btn_15m': "⏱️ 15 daqiqa oldin",
        'notif_btn_off': "🔕 Oʻchirish",
        'notif_saved': "Bildirishnoma sozlamasi saqlandi!",
        'notif_alert_title': "NAMOZ VAQTI BILDIRISHNOMASI",
        'notif_entered': "vaqti kirdi!",
        'notif_verse': "«Albatta, namoz moʻminlarga vaqtida tayinlangan farzdir.» (Niso, 103)",
        'friday_title': "JUMA AYYOMINGIZ MUBORAK BOʻLSIN!",
        'friday_text': "Bugun muborak Juma kuni! Paygʻambarimizga (s.a.v.) koʻproq salovat aytish va Kahf surasini oʻqish sunnatdir. 🤲",
        'downloading': "Media yuklab olinmoqda, iltimos kuting...",
        'uploading': "Telegramga yuklanmoqda...",
        'error_size': "⚠️ Fayl hajmi Telegram Bot cheklovidan (50 MB) katta. Iltimos, qisqaroq video yuboring.",
        'error_general': "Xatolik yuz berdi. Qaytadan urinib koʻring.",
        'schedule_processing': "Qulflangan ekran fon rasmi tayyorlanmoqda...",
        'schedule_ready_caption': "Dars jadvali (Qulflangan ekran)",
        'doc_processing': "Fayl qabul qilindi, ishlov berilmoqda...",
        'video_error': "Videoni yuklab olishda xatolik yuz berdi. Havola yopiq (private) boʻlishi mumkin.",
        'rate_limit_alert': "⚠️ Iltimos, tugmalarni juda tez bosmang.",
        'adhkar_morning_btn': "🌅 Tonggi zikrlar",
        'adhkar_evening_btn': "🌇 Kechki zikrlar",
        'adhkar_salawat_btn': "🤲 Salovatlar",
        'adhkar_morning_text': (
            "🌅 *TONGGI ZIKRLAR (ARABCHA MATN VA MAʼNOSI)*\n\n"
            "1️⃣ *Oyatal Kursiy (Baqara surasi, 255-oyat)*\n"
            "اللَّهُ لَا إِلَٰهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ ۚ لَا تَأْخُذُهُ سِنَةٌ وَلَا نَوْمٌ ۚ لَهُ مَا فِي السَّمَاوَاتِ وَمَا فِي الْأَرْضِ ۗ مَنْ ذَا الَّذِي يَشْفَعُ عِنْدَهُ إِلَّا بِإِذْنِهِ ۚ يَعْلَمُ مَا بَيْنَ أَيْدِيهِمْ وَمَا خَلْفَهُمْ ۖ وَلَا يُحِيطُونَ بِشَيْءٍ مِنْ عِلْمِهِ إِلَّا بِمَا شَاءَ ۚ وَسِعَ كُرْسِيُّهُ السَّمَاوَاتِ وَالْأَرْضَ ۖ وَلَا يَئُودُهُ حِفْظُهُمَا ۚ وَهُوَ الْعَلِيُّ الْعَظِيمُ\n\n"
            "🇺🇿 *Maʼnosi:* «Alloh – Undan oʻzga iloh yoʻqdir. U doim tirik va barchani idora qilib turuvchi (Qayyum)dir. Uni na mudroq bosar va na uyqu. Osmonlar va yerdagi barcha narsa Unikidir. Uning huzurida Oʻz iznisiz kim ham shafoat qila olardi?! U ularning oldilaridagi va orqalaridagi narsalarni biladi. Ular esa Uning ilmidan faqat Oʻzi xohlaganicha narsanigina qamrab oladilar. Uning Kursiysi osmonlar va yerni qamrab olgandir. Ularni asrab-turish Unga ogʻirlik qilmas. U eng yuksak va buyuk zotdir.»\n\n"
            "2️⃣ *Ixlos, Falaq va Nos suralari (3 martadan)*\n"
            "3️⃣ *Sayyidul Istigʻfor (Eng ulugʻ tavba duosi)*\n"
            "اللَّهُمَّ أَنْتَ رَبِّي لَا إِلَٰهَ إِلَّا أَنْتَ، خَلَقْتَنِي وَأَنَا عَبْدُكَ، وَأَنَا عَلَىٰ عَهْدِكَ وَوَعْدِكَ مَا اسْتَطَعْتُ، أَعُوذُ بِكَ مِنْ شَرِّ مَا صَنَعْتُ، أَبُوءُ لَكَ بِنِعْمَتِكَ عَلَيَّ، وَأَبُوءُ لَكَ بِذَنْبِي فَاغْفِرْ لِي، فَإِنَّهُ لَا يَغْفِرُ الذُّنُوبَ إِلَّا أَنْتَ\n\n"
            "4️⃣ *Tonggi hamd va tavhid zikri*\n"
            "أَصْبَحْنَا وَأَصْبَحَ الْمُلْكُ لِلَّهِ، وَالْحَمْدُ لِلَّهِ، لَا إِلَٰهَ إِلَّا اللَّهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَىٰ كُلِّ شَيْءٍ قَدِيرٌ\n\n"
            "5️⃣ *Zararlardan himoyalanish zikri (3 marta)*\n"
            "بِسْمِ اللَّهِ الَّذِي لَا يَضُرُّ مَعَ اسْمِهِ شَيْءٌ فِي الْأَرْضِ وَلَا فِي السَّمَاءِ وَهُوَ السَّمِيعُ الْعَلِيمُ"
        ),
        'adhkar_evening_text': (
            "🌇 *KECHKI ZIKRLAR (ARABCHA MATN VA MAʼNOSI)*\n\n"
            "1️⃣ *Oyatal Kursiy (Baqara surasi, 255-oyat)*\n"
            "2️⃣ *Ixlos, Falaq va Nos suralari (3 martadan)*\n"
            "3️⃣ *Sayyidul Istigʻfor*\n"
            "اللَّهُمَّ أَنْتَ رَبِّي لَا إِلَٰهَ إِلَّا أَنْتَ، خَلَقْتَنِي وَأَنَا عَبْدُكَ، وَأَنَا عَلَىٰ عَهْدِكَ وَوَعْدِكَ مَا اسْتَطَعْتُ، أَعُوذُ بِكَ مِنْ شَرِّ مَا صَنَعْتُ، أَبُوءُ لَكَ بِنِعْمَتِكَ عَلَيَّ، وَأَبُوءُ لَكَ بِذَنْبِي فَاغْفِرْ لِي، فَإِنَّهُ لَا يَغْفِرُ الذُّنُوبَ إِلَّا أَنْتَ\n\n"
            "4️⃣ *Kechki hamd zikri*\n"
            "أَمْسَيْنَا وَأَمْسَى الْمُلْكُ لِلَّهِ، وَالْحَمْدُ لِلَّهِ، لَا إِلَٰهَ إِلَّا اللَّهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَىٰ كُلِّ شَيْءٍ قَدِيرٌ\n\n"
            "5️⃣ *Zararlardan himoyalanish zikri (3 marta)*\n"
            "بِسْمِ اللَّهِ الَّذِي لَا يَضُرُّ مَعَ اسْمِهِ شَيْءٌ فِي الْأَرْضِ وَلَا فِي السَّمَاءِ وَهُوَ السَّمِيعُ الْعَلِيمُ"
        ),
        'adhkar_salawat_text': (
            "🤲 *ENG MUTEBAR SALOVATLAR VA ULARNING MAʼNOLARI*\n\n"
            "1️⃣ *Salovati Ibrohimiyya (Namozdagi salovat)*\n"
            "اللَّهُمَّ صَلِّ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ كَمَا صَلَّيْتَ عَلَى إِبْرَاهِيمَ وَعَلَى آلِ إِبْرَاهِيمَ إِنَّكَ حَمِيدٌ مَجِيدٌ، اللَّهُمَّ بَارِكْ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ كَمَا بَارَكْتَ عَلَى إِبْرَاهِيمَ وَعَلَى آلِ إِبْرَاهِيمَ إِنَّكَ حَمِيدٌ مَجِيدٌ\n\n"
            "2️⃣ *Salovati Tibbil Qulub (Qalblar shifosi)*\n"
            "اللَّهُمَّ صَلِّ عَلَى سَيِّدِنَا مُحَمَّدٍ طِبِّ الْقُلُوبِ وَدَوَائِهَا، وَعَافِيَةِ الْأَبْدَانِ وَشِفَائِهَا، وَنُورِ الْأَبْصَارِ وَضِيَائِهَا، وَعَلَى آلِهِ وَصَحْبِهِ وَسَلِّمْ\n\n"
            "3️⃣ *Salovati Tunjina (Munjiyya)*\n"
            "اللَّهُمَّ صَلِّ عَلَى سَيِّدِنَا مُحَمَّدٍ صَلَاةً تُنْجِينَا بِهَا مِنْ جَمِيعِ الْأَهْوَالِ وَالْآفَاتِ، وَتَقْضِي لَنَا بِهَا جَمِيعَ الْحَاجَاتِ، وَتُطَهِّرُنَا بِهَا مِنْ جَمِيعِ السَّيِّئَاتِ، وَتَرْفَعُنَا بِهَا عِنْدَكَ أَعْلَى الدَّرَجَاتِ، وَتُبَلِّغُنَا بِهَا أَقْصَى الْغَايَاتِ مِنْ جَمِيعِ الْخَيْرَاتِ فِي الْحَيَاةِ وَبَعْدَ الْمَمَاتِ"
        )
    },
    'tr': {
        'btn_qaza_tracker': '📿 Kaza Namazı Defteri',
        'qaza_title': '*NUN PROJECT // KAZA NAMAZI DEFTERİ*',
        'qaza_total': 'Toplam kaza namazı borcu:',
        'btn_ramadan_countdown': '🌙 Sahur & İftar Canlı Sayacı',
        'flood_jail_alert': '⚠️ Çok kısa sürede aşırı sayıda istek gönderdiniz. Güvenlik için lütfen 10 dakika bekleyiniz.',
        'btn_download_audio': '🎵 Sadece Ses (MP3) İndir',
        'audio_downloading': '🎵 Ses dosyası (MP3) hazırlanıyor...',
        'audio_ready': 'Ses dosyası başarıyla hazırlandı!',
        'btn_feedback': "💡 Öneri & Destek",
        'prompt_feedback': "💡 *ÖNERİ & DESTEK BİLDİRİMİ*\n\nNun Bot ile ilgili öneri, dilek veya karşılaştığınız sorunu buraya yazıp gönderin. Mesajınız doğrudan yöneticilerimize iletilecektir:\n\n_(İptal etmek için /cancel)_",
        'feedback_sent': "✅ Bildiriminiz yöneticilere başarıyla iletildi. Teşekkür ederiz!",
        'maintenance_msg': "🚧 *BAKIM VE GÜNCELLEME ÇALIŞMASI*\n\nBotumuzda altyapı iyileştirmesi ve güncelleme yapılmaktadır. Lütfen kısa bir süre sonra tekrar deneyiniz.",
        'banned_msg': "⛔ Botu kullanımınız yönetici tarafından kısıtlanmıştır.",
        'welcome': "Merhaba! Nun Bot'a hoş geldiniz.\nAşağıdaki menüden işlem seçiniz:",
        'menu_title': "📋 Ana Menü:",
        'btn_video': "🎬 Video İndir",
        'btn_prayer': "🕌 Namaz & İbadet",
        'btn_adhkar_hub': "📿 Zikirler & Salavat",
        'btn_daily_hadith': "📖 Günün Hadis-i Şerifi",
        'btn_exam_todo': "📝 Günlük Ders Hedefleri (To-Do)",
        'prompt_todo_add': "✍️ Eklemek istediğiniz ders hedefini yazıp gönderin:\n_(Örneğin: Matematik 20 soru çözümü, Fizik raporunu hazırla)_ ",
        'btn_imsakiye_pdf': "📄 30 Günlük İmsakiye (PDF)",
        'btn_kerahat_info': "⚠️ Kerahat & İşrak",
        'btn_hijri_cal': "🌙 Dini Günler Takvimi",
        'btn_weather': "🌤️ Hava Durumu",
        'btn_pdf_hub': "📄 PDF & Belgeler",
        'btn_exam': "🎓 Sınav & Geri Sayım",
        'btn_schedule_img': "🗓️ Ders Programı",
        'btn_pomodoro': "⏱️ Pomodoro & Sayaç",
        'btn_translit': "🔤 Kiril ⇄ Latin",
        'btn_adhkar': "📿 Zikirler & Salavat",
        'btn_timezone_hub': "🕒 Saat & Konum Ayarı",
        'btn_lang': "🌐 Dil Seçimi",
        'prayer_loading': "⏳ Namaz vakitleri hesaplanıyor...",
        'btn_reset': "🔄 Yenile & Baştan Başlat",
        'reset_success': "🔄 *Bot baştan başlatıldı ve sohbet temizlendi!*\n\nSaat dilimi ve konumunuz korundu; tüm sınavlar, ders hedefleri, hatırlatıcılar ve sohbet geçmişi sıfırlandı.\n\n_Nun Bot hizmetinizdedir:_ ",
        'btn_timezone': "🕒 Saat Dilimi",
        'btn_city_label': "Şehir",
        'btn_auto_loc': "📍 Otomatik Algıla (Konum / Şehir)",
        'btn_change_prayer_city': "🔄 Şehri Değiştir",
        'btn_change_weather_city': "🔄 Başka Şehir Hava Durumu",
        'prompt_video': "🔗 Instagram, TikTok, Facebook, X (Twitter) veya YouTube linki gönderin:",
        'prompt_prayer': "🕌 *NUN PROJECT // NAMAZ VAKİTLERİ*\n\nNamaz vakitlerini öğrenmek istediğiniz şehrin adını yazıp gönderin:\n_(Örneğin: *Kokand*, *İstanbul*, *Ankara*, *Taşkent*...)_",
        'prompt_weather': "🌤️ *NUN PROJECT // HAVA DURUMU HİZMETİ*\n\nHava durumunu öğrenmek istediğiniz il, ilçe veya kasaba adını yazıp gönderin:\n_(Örneğin: *İstanbul*, *Kadıköy*, *Ankara*, *Taşkent*, *Kokand*...)_",
        'prompt_pdf_hub': "📄 *NUN PROJECT // PDF & BELGE ARAÇLARI*\n\nİşlem seçiniz:",
        'prompt_schedule_img': "🗓️ *HAFTALIK DERS PROGRAMI GÖRSELİ*\n\nDers programınızı gün gün yazıp gönderin (Örn: Pazartesi: 09:00 Matematik...):\nBot 1080x1920 telefon kilit ekranı formatına dönüştürecektir.",
        'prompt_pomodoro': "⏱️ *POMODORO & HATIRLATICI MERKEZİ*",
        'prompt_adhkar': "📿 Zikir veya Salavat kategorisini seçiniz:",
        'prompt_translit': "✍️ Metninizi gönderin, otomatik Kiril ⇄ Latin alfabesine dönüştüreyim:",
        'prompt_convert_to_pdf': "📸 *PDF DÖNÜŞTÜRÜCÜ AKTİF*\n\nPDF formatına dönüştürmek istediğiniz dosyayı gönderin:\n_(Fotoğraf, Word .docx, Excel .xlsx veya TXT)_",
        'prompt_ocr': "🔍 *GÖRSELDEN METİN ÇIKARMA (OCR) AKTİF*\n\nMetnini okutmak istediğiniz kitap veya tahta fotoğrafını gönderin:\n_(Arapça, Çince, Rusça, Türkçe, Özbekçe ve tüm diller desteklenir)_",
        'prompt_exam_title': "🎓 *SINAV EKLE*\n\n✍️ Sınav veya dersin adını yazıp gönderin:\n_(Örneğin: *Yüksek Matematik*, *Fizik Final*)_",
        'prompt_remind': "⏰ Hatırlatıcıyı şu formatta gönderin:\n`Kitap oku - 18:30` veya `Ders - 30 dakika`",
        'prompt_timezone': "🕒 *SAAT DİLİMİ VE KONUM AYARI*",
        'prompt_send_location': "📍 *KONUM / ŞEHİR BİLGİSİ*\n\nLütfen Telegram üzerinden konumunuzu (Location) gönderin veya şehrinizi yazın (Örn: *İstanbul*, *Taşkent*, *Ankara*, *Moskova*...):",
        'prompt_city_sync_title': "ŞEHİR VEYA KONUMUNUZU BELİRTİN",
        'prompt_city_sync_desc': "Namaz vakitleri, sınav geri sayımları ve hatırlatıcıların tam yerel saatinize göre çalışabilmesi için şu anda hangi şehirdesiniz?\n_(Şehir adı yazabilir, örneğin: *İstanbul*, *Ankara*, *Taşkent* veya konum gönderebilirsiniz)_",
        'tz_hub_instruction': "Aşağıdaki butonlardan şehrinizi/saat diliminizi seçebilir veya doğrudan yeni bir şehir adı yazabilirsiniz:",
        'tz_prayer_synced_lbl': "namaz vakitleriyle senkronize edildi!",
        'tz_loc_detected': "KONUM VE SAAT DİLİMİ ALGILANDI",
        'tz_synced_hint': "Tüm zamanlayıcılar, hatırlatıcılar ve namaz vakitleri yerel saatinize göre tam senkronize edildi.",
        'current_time_lbl': "Güncel Saatiniz",
        'pomo_started': "POMODORO BAŞLADI",
        'pomo_work_label': "Çalışma",
        'pomo_break_label': "Mola",
        'pomo_mins_unit': "dk",
        'pomo_mode_lbl': "Mod",
        'pomo_dur_lbl': "Süre",
        'pomo_end_lbl': "Bitiş Saati",
        'pomo_break_over': "MOLA BİTTİ!",
        'pomo_work_over': "POMODORO TAMAMLANDI!",
        'pomo_btn_work25': "🍅 25 Dk Çalış",
        'pomo_btn_break5': "☕ 5 Dk Mola",
        'pomo_btn_work50': "🍅 50 Dk Çalış",
        'pomo_btn_break10': "☕ 10 Dk Mola",
        'pomo_btn_add_remind': "⏰ Yeni Hatırlatıcı Ekle",
        'pomo_btn_my_reminds': "📋 Hatırlatıcılarım",
        'exam_empty': "Henüz kayıtlı bir sınavınız bulunmuyor.",
        'exam_btn_add': "➕ Sınav Ekle",
        'exam_saved': "Sınav başarıyla kaydedildi!",
        'exam_deleted': "Sınav başarıyla silindi.",
        'exam_default_title': "Sınav",
        'exam_hub_title': "🎓 *NUN PROJECT // SINAV GERİ SAYIMI*",
        'remind_saved': "Hatırlatıcı kuruldu!",
        'remind_empty': "Aktif hatırlatıcınız bulunmuyor.",
        'remind_due': "NUN PROJECT // HATIRLATICI",
        'remind_deleted': "Hatırlatıcı başarıyla silindi.",
        'remind_task_lbl': "Görev",
        'remind_note': "Belirlenen vakit geldi!",
        'pdf_ready': "PDF belgesi başarıyla hazırlandı!",
        'pdf_fail': "Dosyayı PDF'e dönüştürürken bir hata oluştu.",
        'pdf_hub_to_pdf_btn': "📸 Fotoğraf / Word / Excel / TXT ➔ PDF",
        'pdf_hub_ocr_btn': "🔍 Görselden Metin Çıkarma (OCR)",
        'ocr_title': "GÖRSELDEN OKUNAN METİN:",
        'ocr_fail': "Görselden okunabilir bir metin bulunamadı.",
        'direct_img_prompt': "Fotoğraf alındı. Hangi işlemi yapmak istersiniz?",
        'btn_direct_pdf': "PDF'e Dönüştür",
        'btn_direct_ocr': "Metni Oku (OCR)",
        'btn_cancel': "İptal",
        'cancel_success': "İşlem iptal edildi.",
        'lang_changed': "Dil başarıyla değiştirildi!",
        'tz_changed': "Saat dilimi başarıyla güncellendi!",
        'city_not_found': "Şehir bulunamadı. Lütfen şehir adını doğru yazın.",
        'weather_loading': "🌤️ Hava durumu bilgisi alınıyor...",
        'weather_city_not_found': "Konum bulunamadı. Lütfen il veya ilçe adını kontrol edip tekrar yazın.",
        'loc_prompt_multimatch': "📍 *BİRDEN FAZLA KONUM BULUNDU*\n\nLütfen aradığınız doğru bölgeyi seçiniz:",
        'btn_prayer_notif': "🔔 Ezan & Vakit Bildirimi",
        'notif_menu_title': "🔔 *NAMAZ VAKTİ BİLDİRİMLERİ*",
        'notif_menu_desc': "Namaz vakitleri girdiğinde botun size otomatik bildirim göndermesini ister misiniz?\nİstediğiniz modu seçiniz:",
        'notif_btn_on_time': "✅ Tam Vaktinde (Ezan)",
        'notif_btn_15m': "⏱️ 15 Dakika Önce",
        'notif_btn_off': "🔕 Kapat",
        'notif_saved': "Bildirim ayarınız kaydedildi!",
        'notif_alert_title': "NAMAZ VAKTİ BİLDİRİMİ",
        'notif_entered': "vakti girdi!",
        'notif_verse': "«Şüphesiz namaz, mü'minler üzerine vakitleri belirlenmiş bir farzdır.» (Nisâ, 103)",
        'friday_title': "HAYIRLI CUMALAR!",
        'friday_text': "Bugün mübarek Cuma günü! Peygamber Efendimiz'e (s.a.v.) bolca salavat getirmeyi ve Kehf suresini okumayı unutmayınız. 🤲",
        'downloading': "Medya indiriliyor, lütfen bekleyin...",
        'uploading': "Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından büyük olduğu için gönderilemiyor.",
        'error_general': "Bir hata oluştu. Lütfen tekrar deneyin.",
        'schedule_processing': "Kilit ekranı duvar kağıdı hazırlanıyor...",
        'schedule_ready_caption': "Ders Programı (Kilit Ekranı)",
        'doc_processing': "Dosya alındı, işleniyor...",
        'video_error': "Video indirilirken bir hata oluştu. Bağlantı gizli hesapta olabilir.",
        'rate_limit_alert': "⚠️ Lütfen butonlara çok hızlı tıklamayınız.",
        'adhkar_morning_btn': "🌅 Sabah Zikirleri",
        'adhkar_evening_btn': "🌇 Akşam Zikirleri",
        'adhkar_salawat_btn': "🤲 Salavatlar",
        'adhkar_morning_text': (
            "🌅 *SABAH ZİKİRLERİ (ARAPÇA METİN VE MEAL)*\n\n"
            "1️⃣ *Ayet-el Kürsi (Bakara Suresi, 255. Ayet)*\n"
            "اللَّهُ لَا إِلَٰهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ ۚ لَا تَأْخُذُهُ سِنَةٌ وَلَا نَوْمٌ ۚ لَهُ مَا فِي السَّمَاوَاتِ وَمَا فِي الْأَرْضِ ۗ مَنْ ذَا الَّذِي يَشْفَعُ عِنْدَهُ إِلَّا بِإِذْنِهِ ۚ يَعْلَمُ مَا بَيْنَ أَيْدِيهِمْ وَمَا خَلْفَهُمْ ۖ وَلَا يُحِيطُونَ بِشَيْءٍ مِنْ عِلْمِهِ إِلَّا بِمَا شَاءَ ۚ وَسِعَ كُرْسِيُّهُ السَّمَاوَاتِ وَالْأَرْضَ ۖ وَلَا يَئُودُهُ حِفْظُهُمَا ۚ وَهُوَ الْعَلِيُّ الْعَظِيمُ\n\n"
            "🇹🇷 *Meali:* «Allah, O'ndan başka ilah yoktur; diridir, her şeyin varlığı O'na bağlı ve dayalıdır. O'nu ne uyuklama tutar ne de uyku. Göklerde ve yerde ne varsa hepsi O'nundur. İzni olmadan O'nun huzurunda kim şefaat edebilir? O, kullarının önlerindekini de arkalarındakini de (yaptıklarını ve yapacaklarını) bilir. Kullar O'nun ilminden, kendisinin dilediğinden başka hiçbir şeyi kavrayamazlar. O'nun kürsüsü gökleri ve yeri kaplamıştır. Onları koruyup gözetmek O'na asla ağır gelmez. O, yücedir, büyüktür.»\n\n"
            "2️⃣ *İhlas, Felak ve Nas Sureleri (3 defa)*\n"
            "3️⃣ *Seyyidü'l-İstiğfar (En Faziletli Tövbe Duası)*\n"
            "اللَّهُمَّ أَنْتَ رَبِّي لَا إِلَٰهَ إِلَّا أَنْتَ، خَلَقْتَنِي وَأَنَا عَبْدُكَ، وَأَنَا عَلَىٰ عَهْدِكَ وَوَعْدِكَ مَا اسْتَطَعْتُ، أَعُوذُ بِكَ مِنْ شَرِّ مَا صَنَعْتُ، أَبُوءُ لَكَ بِنِعْمَتِكَ عَلَيَّ، وَأَبُوءُ لَكَ بِذَنْبِي فَاغْفِرْ لِي، فَإِنَّهُ لَا يَغْفِرُ الذُّنُوبَ إِلَّا أَنْتَ\n\n"
            "4️⃣ *Sabah Hamd ve Tevhid Zikri*\n"
            "أَصْبَحْنَا وَأَصْبَحَ الْمُلْكُ لِلَّهِ، وَالْحَمْدُ لِلَّهِ، لَا إِلَٰهَ إِلَّا اللَّهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَىٰ كُلِّ شَيْءٍ قَدِيرٌ\n\n"
            "5️⃣ *Zararlardan Korunma Duası (3 defa)*\n"
            "بِسْمِ اللَّهِ الَّذِي لَا يَضُرُّ مَعَ اسْمِهِ شَيْءٌ فِي الْأَرْضِ وَلَا فِي السَّمَاءِ وَهُوَ السَّمِيعُ الْعَلِيمُ"
        ),
        'adhkar_evening_text': (
            "🌇 *AKŞAM ZİKİRLERİ (ARAPÇA METİN VE MEAL)*\n\n"
            "1️⃣ *Ayet-el Kürsi (Bakara Suresi, 255. Ayet)*\n"
            "2️⃣ *İhlas, Felak ve Nas Sureleri (3 defa)*\n"
            "3️⃣ *Seyyidü'l-İstiğfar*\n"
            "اللَّهُمَّ أَنْتَ رَبِّي لَا إِلَٰهَ إِلَّا أَنْتَ، خَلَقْتَنِي وَأَنَا عَبْدُكَ، وَأَنَا عَلَىٰ عَهْدِكَ وَوَعْدِكَ مَا اسْتَطَعْتُ، أَعُوذُ بِكَ مِنْ شَرِّ مَا صَنَعْتُ، أَبُوءُ لَكَ بِنِعْمَتِكَ عَلَيَّ، وَأَبُوءُ لَكَ بِذَنْبِي فَاغْفِرْ لِي، فَإِنَّهُ لَا يَغْفِرُ الذُّنُوبَ إِلَّا أَنْتَ\n\n"
            "4️⃣ *Akşam Hamd Zikri*\n"
            "أَمْسَيْنَا وَأَمْسَى الْمُلْكُ لِلَّهِ، وَالْحَمْدُ لِلَّهِ، لَا إِلَٰهَ إِلَّا اللَّهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَىٰ كُلِّ شَيْءٍ قَدِيرٌ\n\n"
            "5️⃣ *Zararlardan Korunma Duası (3 defa)*\n"
            "بِسْمِ اللَّهِ الَّذِي لَا يَضُرُّ مَعَ اسْمِهِ شَيْءٌ فِي الْأَرْضِ وَلَا فِي السَّمَاءِ وَهُوَ السَّمِيعُ الْعَلِيمُ"
        ),
        'adhkar_salawat_text': (
            "🤲 *EN MUTEBER SALAVATLAR VE MEALLERİ*\n\n"
            "1️⃣ *Salavat-ı İbrahimiye (Namazdaki Salli-Barik)*\n"
            "اللَّهُمَّ صَلِّ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ كَمَا صَلَّيْتَ عَلَى إِبْرَاهِيمَ وَعَلَى آلِ إِبْرَاهِيمَ إِنَّكَ حَمِيدٌ مَجِيدٌ، اللَّهُمَّ بَارِكْ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ كَمَا بَارَكْتَ عَلَى إِبْرَاهِيمَ وَعَلَى آلِ إِبْرَاهِيمَ إِنَّكَ حَمِيدٌ مَجِيدٌ\n\n"
            "2️⃣ *Salavat-ı Tıbbi'l-Kulûb (Şifa Salavatı)*\n"
            "اللَّهُمَّ صَلِّ عَلَى سَيِّدِنَا مُحَمَّدٍ طِبِّ الْقُلُوبِ وَدَوَائِهَا، وَعَافِيَةِ الْأَبْدَانِ وَشِفَائِهَا، وَنُورِ الْأَبْصَارِ وَضِيَائِهَا، وَعَلَى آلِهِ وَصَحْبِهِ وَسَلِّمْ\n\n"
            "3️⃣ *Salavat-ı Münciye (Tüncina Duası)*\n"
            "اللَّهُمَّ صَلِّ عَلَى سَيِّدِنَا مُحَمَّدٍ صَلَاةً تُنْجِينَا بِهَا مِنْ جَمِيعِ الْأَهْوَالِ وَالْآفَاتِ، وَتَقْضِي لَنَا بِهَا جَمِيعَ الْحَاجَاتِ، وَتُطَهِّرُنَا بِهَا مِنْ جَمِيعِ السَّيِّئَاتِ، وَتَرْفَعُنَا بِهَا عِنْدَكَ أَعْلَى الدَّرَجَاتِ، وَتُبَلِّغُنَا بِهَا أَقْصَى الْغَايَاتِ مِنْ جَمِيعِ الْخَيْرَاتِ فِي الْحَيَاةِ وَبَعْدَ الْمَمَاتِ"
        )
    },
    'ru': {
        'btn_qaza_tracker': '📿 Учет пропущенных намазов',
        'qaza_title': '*NUN PROJECT // УЧЕТ ПРОПУЩЕННЫХ НАМАЗОВ*',
        'qaza_total': 'Всего пропущенных намазов:',
        'btn_ramadan_countdown': '🌙 Счетчик Сухура и Ифтара',
        'flood_jail_alert': '⚠️ Вы отправили слишком много запросов за короткое время. Подождите 10 минут.',
        'btn_download_audio': '🎵 Скачать только аудио (MP3)',
        'audio_downloading': '🎵 Извлечение аудио (MP3)...',
        'audio_ready': 'Аудиофайл готов!',
        'btn_feedback': "💡 Отзыв и Поддержка",
        'prompt_feedback': "💡 *ОТЗЫВЫ И ПОДДЕРЖКА*\n\nНапишите ваш отзыв, пожелание или найденную ошибку. Ваше сообщение будет напрямую передано администрации бота:\n\n_(Для отмены напишите /cancel)_",
        'feedback_sent': "✅ Ваше сообщение успешно передано администрации. Спасибо!",
        'maintenance_msg': "🚧 *ТЕХНИЧЕСКИЕ РАБОТЫ*\n\nВедутся работы по оптимизации и обновлению бота. Пожалуйста, попробуйте позже.",
        'banned_msg': "⛔ Ваш доступ к боту заблокирован администратором.",
        'welcome': "Здравствуйте! Добро пожаловать в Nun Bot.\nВыберите действие в меню:",
        'menu_title': "📋 Главное меню:",
        'btn_video': "🎬 Скачать видео",
        'btn_prayer': "🕌 Намаз и Ибадат",
        'btn_adhkar_hub': "📿 Зикры и Салаваты",
        'btn_daily_hadith': "📖 Хадис дня",
        'btn_exam_todo': "📝 Задачи на день (To-Do)",
        'prompt_todo_add': "✍️ Напишите учебную задачу на сегодня:\n_(Например: Решить 20 задач по математике, повторить конспект)_ ",
        'btn_imsakiye_pdf': "📄 Расписание на 30 дней",
        'btn_kerahat_info': "⚠️ Карахат и Ишрак",
        'btn_hijri_cal': "🌙 Мусульманский календарь",
        'btn_weather': "🌤️ Прогноз погоды",
        'btn_pdf_hub': "📄 PDF и Документы",
        'btn_exam': "🎓 Экзамены & Таймер",
        'btn_schedule_img': "🗓️ Расписание уроков",
        'btn_pomodoro': "⏱️ Помодоро & Таймер",
        'btn_translit': "🔤 Кирилл ⇄ Латиница",
        'btn_adhkar': "📿 Зикры и Салаваты",
        'btn_timezone_hub': "🕒 Время и Геолокация",
        'btn_lang': "🌐 Сменить язык",
        'prayer_loading': "⏳ Идет расчет времени намаза...",
        'btn_reset': "🔄 Сброс и Перезапуск",
        'reset_success': "🔄 *Бот перезапущен и чат очищен!*\n\nВаше время и город сохранены; все задачи, экзамены, напоминания и история сообщений сброшены.\n\n_Nun Bot готов к работе:_ ",
        'btn_timezone': "🕒 Часовой пояс",
        'btn_city_label': "Город",
        'btn_auto_loc': "📍 Автоопределение (Гео / Город)",
        'btn_change_prayer_city': "🔄 Сменить город",
        'btn_change_weather_city': "🔄 Сменить город для погоды",
        'prompt_video': "🔗 Отправьте ссылку из Instagram, TikTok, Facebook, X (Twitter) или YouTube:",
        'prompt_prayer': "🕌 *NUN PROJECT // ВРЕМЯ НАМАЗА*\n\nНапишите название города:\n_(Например: *Коканд*, *Ташкент*, *Москва*, *Стамбул*...)_",
        'prompt_weather': "🌤️ *NUN PROJECT // ПРОГНОЗ ПОГОДЫ*\n\nНапишите название города, района или населенного пункта:\n_(Например: *Москва*, *Ташкент*, *Коканд*, *Стамбул*...)_",
        'prompt_pdf_hub': "📄 *NUN PROJECT // PDF & ДОКУМЕНТЫ*\n\nВыберите действие:",
        'prompt_schedule_img': "🗓️ *РАСПИСАНИЕ ЗАНЯТИЙ (ОБОИ)*\n\nОтправьте расписание по дням (Напр: Понедельник: 09:00 Математика...):\nБот создаст стильные обои 1080x1920 для экрана блокировки.",
        'prompt_pomodoro': "⏱️ *ПОМОДОРО И НАПОМИНАНИЯ*",
        'prompt_adhkar': "📿 Выберите категорию зикров или салаватов:",
        'prompt_translit': "✍️ Отправьте текст, автоматически переведу Кириллица ⇄ Латиница:",
        'prompt_convert_to_pdf': "📸 *КОНВЕРТЕР В PDF АКТИВЕН*\n\nОтправьте файл для конвертации в PDF:\n_(Фото, Word .docx, Excel .xlsx или TXT)_",
        'prompt_ocr': "🔍 *ИЗВЛЕЧЕНИЕ ТЕКСТА (OCR) АКТИВНО*\n\nОтправьте фото книги, конспекта или доски:\n_(Поддерживаются арабский, китайский, русский, узбекский, английский и все языки)_",
        'prompt_exam_title': "🎓 *ДОБАВЛЕНИЕ ЭКЗАМЕНА*\n\n✍️ Напишите название предмета или экзамена:\n_(Например: *Высшая Математика*, *Физика*)_",
        'prompt_remind': "⏰ Отправьте напоминание в формате:\n`Читать книгу - 18:30` или `Учеба - 30 минут`",
        'prompt_timezone': "🕒 *ЧАСОВОЙ ПОЯС И ГЕОЛОКАЦИЯ*",
        'prompt_send_location': "📍 *ОТПРАВЬТЕ ГЕОЛОКАЦИЮ ИЛИ ГОРОД*\n\nОтправьте геолокацию (Location) в Telegram или напишите город (Напр: *Москва*, *Ташкент*, *Стамбул*, *Лондон*...):",
        'prompt_city_sync_title': "УКАЖИТЕ ВАШ ГОРОД ИЛИ ГЕОЛОКАЦИЮ",
        'prompt_city_sync_desc': "Чтобы время намаза, таймеры и напоминания работали строго по вашему местному времени, напишите название вашего города или отправьте геолокацию (Location):\n_(Например: *Москва*, *Ташкент*, *Стамбул*, *Самарканд*...)_",
        'tz_hub_instruction': "Выберите часовой пояс из кнопок ниже или отправьте название города:",
        'tz_prayer_synced_lbl': "время намаза синхронизировано!",
        'tz_loc_detected': "ЛОКАЦИЯ И ЧАСОВОЙ ПОЯС ОПРЕДЕЛЕНЫ",
        'tz_synced_hint': "Все таймеры, напоминания и расписание намаза полностью синхронизированы с вашим местным временем.",
        'current_time_lbl': "Ваше местное время",
        'pomo_started': "ПОМОДОРО ЗАПУЩЕН",
        'pomo_work_label': "Работа",
        'pomo_break_label': "Перерыв",
        'pomo_mins_unit': "мин",
        'pomo_mode_lbl': "Режим",
        'pomo_dur_lbl': "Длительность",
        'pomo_end_lbl': "Окончание",
        'pomo_break_over': "ПЕРЕРЫВ ОКОНЧЕН!",
        'pomo_work_over': "ПОМОДОРО ЗАВЕРШЕН!",
        'pomo_btn_work25': "🍅 25 Мин Работа",
        'pomo_btn_break5': "☕ 5 Мин Перерыв",
        'pomo_btn_work50': "🍅 50 Мин Работа",
        'pomo_btn_break10': "☕ 10 Мин Перерыв",
        'pomo_btn_add_remind': "⏰ Новое напоминание",
        'pomo_btn_my_reminds': "📋 Мои напоминания",
        'exam_empty': "У вас пока нет сохраненных экзаменов.",
        'exam_btn_add': "➕ Добавить экзамен",
        'exam_saved': "Экзамен успешно сохранен!",
        'exam_deleted': "Экзамен успешно удален.",
        'exam_default_title': "Экзамен",
        'exam_hub_title': "🎓 *NUN PROJECT // ТАЙМЕР ЭКЗАМЕНОВ*",
        'remind_saved': "Напоминание установлено!",
        'remind_empty': "У вас нет активных напоминаний.",
        'remind_due': "NUN PROJECT // НАПОМИНАНИЕ",
        'remind_deleted': "Напоминание успешно удалено.",
        'remind_task_lbl': "Задача",
        'remind_note': "Время пришло!",
        'pdf_ready': "PDF документ успешно сформирован!",
        'pdf_fail': "Ошибка при конвертации в PDF.",
        'pdf_hub_to_pdf_btn': "📸 Фото / Word / Excel / TXT ➔ PDF",
        'pdf_hub_ocr_btn': "🔍 Извлечение текста (OCR)",
        'ocr_title': "ТЕКСТ, РАСПОЗНАННЫЙ С ИЗОБРАЖЕНИЯ:",
        'ocr_fail': "Разборчивый текст на изображении не найден.",
        'direct_img_prompt': "Изображение получено. Что вы хотите сделать?",
        'btn_direct_pdf': "Конвертировать в PDF",
        'btn_direct_ocr': "Распознать текст (OCR)",
        'btn_cancel': "Отмена",
        'cancel_success': "Действие отменено.",
        'lang_changed': "Язык успешно изменен!",
        'tz_changed': "Часовой пояс успешно обновлен!",
        'city_not_found': "Город не найден. Напишите правильное название.",
        'weather_loading': "🌤️ Получение прогноза погоды...",
        'weather_city_not_found': "Населенный пункт не найден. Проверьте правильность написания.",
        'loc_prompt_multimatch': "📍 *НАЙДЕНО НЕСКОЛЬКО МЕСТ*\n\nПожалуйста, выберите нужный населенный пункт:",
        'btn_prayer_notif': "🔔 Уведомления о намазе",
        'notif_menu_title': "🔔 *УВЕДОМЛЕНИЯ О ВРЕМЕНИ НАМАЗА*",
        'notif_menu_desc': "Хотите получать автоматические напоминания при наступлении времени намаза?\nВыберите режим:",
        'notif_btn_on_time': "✅ Точно вовремя",
        'notif_btn_15m': "⏱️ За 15 минут до",
        'notif_btn_off': "🔕 Отключить",
        'notif_saved': "Настройки уведомлений сохранены!",
        'notif_alert_title': "УВЕДОМЛЕНИЕ О НАМАЗЕ",
        'notif_entered': "время наступило!",
        'notif_verse': "«Воистину, намаз предписан верующим в определенное время.» (Ан-Ниса, 103)",
        'friday_title': "БЛАГОСЛОВЕННОЙ ПЯТНИЦЫ!",
        'friday_text': "Сегодня благословенная пятница! Не забудьте произносить больше салаватов Пророку (мир ему) и читать суру Аль-Кахф. 🤲",
        'downloading': "Скачивается, пожалуйста подождите...",
        'uploading': "Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "Произошла ошибка. Попробуйте снова.",
        'schedule_processing': "Создаются обои для экрана блокировки...",
        'schedule_ready_caption': "Расписание занятий (Экран блокировки)",
        'doc_processing': "Файл получен, обрабатывается...",
        'video_error': "Произошла ошибка при загрузке видео.",
        'rate_limit_alert': "⚠️ Пожалуйста, не нажимайте кнопки слишком часто.",
        'adhkar_morning_btn': "🌅 Утренние зикры",
        'adhkar_evening_btn': "🌇 Вечерние зикры",
        'adhkar_salawat_btn': "🤲 Салаваты",
        'adhkar_morning_text': (
            "🌅 *УТРЕННИЕ ЗИКРЫ (АРАБСКИЙ ТЕКСТ И ПЕРЕВОД)*\n\n"
            "1️⃣ *Аят аль-Курси (Сура аль-Бакара, 255 аят)*\n"
            "اللَّهُ لَا إِلَٰهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ ۚ لَا تَأْخُذُهُ سِنَةٌ وَلَا نَوْمٌ ۚ لَهُ مَا فِي السَّمَاوَاتِ وَمَا فِي الْأَرْضِ ۗ مَنْ ذَا الَّذِي يَشْفَعُ عِنْدَهُ إِلَّا بِإِذْنِهِ ۚ يَعْلَمُ مَا بَيْنَ أَيْدِيهِمْ وَمَا خَلْفَهُمْ ۖ وَلَا يُحِيطُونَ بِشَيْءٍ مِنْ عِلْمِهِ إِلَّا بِمَا شَاءَ ۚ وَسِعَ كُرْسِيُّهُ السَّمَاوَاتِ وَالْأَرْضَ ۖ وَلَا يَئُودُهُ حِفْظُهُمَا ۚ وَهُوَ الْعَلِيُّ الْعَظِيمُ\n\n"
            "2️⃣ *Суры аль-Ихляс, аль-Фаляк, ан-Нас (по 3 раза)*\n"
            "3️⃣ *Саййид аль-Истигфар (Господин покаяния)*\n"
            "اللَّهُمَّ أَنْتَ رَبِّي لَا إِلَٰهَ إِلَّا أَنْتَ، خَلَقْتَنِي وَأَنَا عَبْدُكَ، وَأَنَا عَلَىٰ عَهْدِكَ وَوَعْدِكَ مَا اسْتَطَعْتُ، أَعُوذُ بِكَ مِنْ شَرِّ مَا صَنَعْتُ، أَبُوءُ لَكَ بِنِعْمَتِكَ عَلَيَّ، وَأَبُوءُ لَكَ بِذَنْبِي فَاغْفِرْ لِي، فَإِنَّهُ لَا يَغْفِرُ الذُّنُوبَ إِلَّا أَنْتَ"
        ),
        'adhkar_evening_text': (
            "🌇 *ВЕЧЕРНИЕ ЗИКРЫ (АРАБСКИЙ ТЕКСТ И ПЕРЕВОД)*\n\n"
            "1️⃣ *Аят аль-Курси*\n"
            "2️⃣ *Суры аль-Ихляс, аль-Фаляк, ан-Нас (по 3 раза)*\n"
            "3️⃣ *Саййид аль-Истигфар*"
        ),
        'adhkar_salawat_text': (
            "🤲 *ДОСТОВЕРНЫЕ САЛАВАТЫ И ИХ ЗНАЧЕНИЯ*\n\n"
            "1️⃣ *Салават Ибрахимийя (из намаза)*\n"
            "اللَّهُمَّ صَلِّ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ كَمَا صَلَّيْتَ عَلَى إِبْرَاهِيمَ وَعَلَى آلِ إِبْرَاهِيمَ إِنَّكَ حَمِيدٌ مَجِيدٌ، اللَّهُمَّ بَارِكْ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ كَمَا بَارَكْتَ عَلَى إِبْرَاهِيمَ وَعَلَى آلِ إِبْرَاهِيمَ إِنَّكَ حَمِيدٌ مَجِيدٌ"
        )
    },
    'en': {
        'btn_qaza_tracker': '📿 Missed Prayers Tracker',
        'qaza_title': '*NUN PROJECT // MISSED PRAYERS TRACKER*',
        'qaza_total': 'Total missed prayers:',
        'btn_ramadan_countdown': '🌙 Suhoor & Iftar Countdown',
        'flood_jail_alert': '⚠️ Too many requests sent in a short time. For security, please wait 10 minutes.',
        'btn_download_audio': '🎵 Download Audio (MP3) Only',
        'audio_downloading': '🎵 Extracting audio (MP3)...',
        'audio_ready': 'Audio successfully extracted!',
        'btn_feedback': "💡 Feedback & Support",
        'prompt_feedback': "💡 *FEEDBACK & SUPPORT*\n\nPlease write your suggestions, questions, or report an issue. Your message will be sent directly to the bot administrators:\n\n_(Type /cancel to cancel)_",
        'feedback_sent': "✅ Your feedback has been sent to the admins. Thank you!",
        'maintenance_msg': "🚧 *MAINTENANCE IN PROGRESS*\n\nSystem optimization and updates are currently underway. Please check back in a few minutes.",
        'banned_msg': "⛔ Your access to this bot has been suspended by an administrator.",
        'welcome': "Hello! Welcome to Nun Bot.\nChoose an option from the menu:",
        'menu_title': "📋 Main Menu:",
        'btn_video': "🎬 Download Video",
        'btn_prayer': "🕌 Prayer & Worship",
        'btn_adhkar_hub': "📿 Adhkar & Salawat",
        'btn_daily_hadith': "📖 Daily Hadith",
        'btn_exam_todo': "📝 Daily Study Goals (To-Do)",
        'prompt_todo_add': "✍️ Type your study goal or task for today:\n_(e.g. Solve 20 Math problems, read Biology chapter 3)_ ",
        'btn_imsakiye_pdf': "📄 30-Day Timetable (PDF)",
        'btn_kerahat_info': "⚠️ Makruh & Ishraq",
        'btn_hijri_cal': "🌙 Islamic Calendar & Events",
        'btn_weather': "🌤️ Weather Forecast",
        'btn_pdf_hub': "📄 PDF & Documents",
        'btn_exam': "🎓 Exams & Countdown",
        'btn_schedule_img': "🗓️ Class Schedule",
        'btn_pomodoro': "⏱️ Pomodoro & Timer",
        'btn_translit': "🔤 Cyrillic ⇄ Latin",
        'btn_adhkar': "📿 Adhkar & Salawat",
        'btn_timezone_hub': "🕒 Time & Location",
        'btn_lang': "🌐 Change Language",
        'prayer_loading': "⏳ Calculating prayer times...",
        'btn_reset': "🔄 Refresh & Reset",
        'reset_success': "🔄 *Bot restarted and chat cleared!*\n\nYour timezone and location were preserved; all tasks, exams, reminders, and chat history have been wiped.\n\n_Nun Bot is ready:_ ",
        'btn_timezone': "🕒 Timezone",
        'btn_city_label': "City",
        'btn_auto_loc': "📍 Auto-Detect (Location / City)",
        'btn_change_prayer_city': "🔄 Change City",
        'btn_change_weather_city': "🔄 Change Weather City",
        'prompt_video': "🔗 Send a link from Instagram, TikTok, Facebook, X (Twitter), or YouTube:",
        'prompt_prayer': "🕌 *NUN PROJECT // PRAYER TIMES*\n\nType the city name:\n_(e.g. *Kokand*, *Tashkent*, *Istanbul*, *London*...)_",
        'prompt_weather': "🌤️ *NUN PROJECT // WEATHER SERVICE*\n\nType the name of any city, district, or town:\n_(e.g. *London*, *Istanbul*, *Tashkent*, *Kokand*, *New York*...)_",
        'prompt_pdf_hub': "📄 *NUN PROJECT // PDF & DOCUMENTS HUB*\n\nChoose an action:",
        'prompt_schedule_img': "🗓️ *WEEKLY SCHEDULE WALLPAPER*\n\nSend your schedule line by line (e.g. Monday: 09:00 Math...):\nThe bot will generate an aesthetic 1080x1920 lock-screen wallpaper.",
        'prompt_pomodoro': "⏱️ *POMODORO & REMINDERS HUB*",
        'prompt_adhkar': "📿 Choose adhkar or salawat category:",
        'prompt_translit': "✍️ Send your text to convert Cyrillic ⇄ Latin:",
        'prompt_convert_to_pdf': "📸 *CONVERT TO PDF ACTIVE*\n\nSend the file you want to convert to PDF:\n_(Image, Word .docx, Excel .xlsx, or TXT)_",
        'prompt_ocr': "🔍 *TEXT EXTRACTION (OCR) ACTIVE*\n\nSend a photo of a whiteboard, book, or notes:\n_(Arabic, Chinese, Russian, Turkish, Uzbek, English and all languages supported)_",
        'prompt_exam_title': "🎓 *ADD EXAM*\n\n✍️ Type the subject or exam title:\n_(e.g. *Calculus Final*, *Physics*)_",
        'prompt_remind': "⏰ Send reminder in format:\n`Read book - 18:30` or `Study - 30 minutes`",
        'prompt_timezone': "🕒 *TIMEZONE & LOCATION SETTINGS*",
        'prompt_send_location': "📍 *SHARE LOCATION OR CITY*\n\nPlease share your Location via Telegram or type your city name (e.g. *London*, *Istanbul*, *Tashkent*, *New York*...):",
        'prompt_city_sync_title': "SET YOUR CITY OR LOCATION",
        'prompt_city_sync_desc': "To accurately sync prayer times, timers, and countdowns to your exact local time, what city are you currently in?\n_(Type your city name e.g. *London*, *Istanbul*, *Tashkent* or send Location)_",
        'tz_hub_instruction': "Select your city/timezone below or simply type a new city name:",
        'tz_prayer_synced_lbl': "prayer times synced!",
        'tz_loc_detected': "LOCATION & TIMEZONE DETECTED",
        'tz_synced_hint': "All timers, reminders, and prayer times are now accurately aligned with your local time.",
        'current_time_lbl': "Your Local Time",
        'pomo_started': "POMODORO STARTED",
        'pomo_work_label': "Work",
        'pomo_break_label': "Break",
        'pomo_mins_unit': "min",
        'pomo_mode_lbl': "Mode",
        'pomo_dur_lbl': "Duration",
        'pomo_end_lbl': "Ends at",
        'pomo_break_over': "BREAK OVER!",
        'pomo_work_over': "POMODORO FINISHED!",
        'pomo_btn_work25': "🍅 25 Min Work",
        'pomo_btn_break5': "☕ 5 Min Break",
        'pomo_btn_work50': "🍅 50 Min Work",
        'pomo_btn_break10': "☕ 10 Min Break",
        'pomo_btn_add_remind': "⏰ Add New Reminder",
        'pomo_btn_my_reminds': "📋 My Reminders",
        'exam_empty': "You don't have any saved exams yet.",
        'exam_btn_add': "➕ Add Exam",
        'exam_saved': "Exam successfully saved!",
        'exam_deleted': "Exam successfully deleted.",
        'exam_default_title': "Exam",
        'exam_hub_title': "🎓 *NUN PROJECT // EXAM COUNTDOWN*",
        'remind_saved': "Reminder set!",
        'remind_empty': "You have no active reminders.",
        'remind_due': "NUN PROJECT // REMINDER",
        'remind_deleted': "Reminder successfully deleted.",
        'remind_task_lbl': "Task",
        'remind_note': "Time is up!",
        'pdf_ready': "PDF document successfully generated!",
        'pdf_fail': "Failed to convert file to PDF.",
        'pdf_hub_to_pdf_btn': "📸 Photo / Word / Excel / TXT ➔ PDF",
        'pdf_hub_ocr_btn': "🔍 Extract Text (OCR)",
        'ocr_title': "TEXT RECOGNIZED FROM IMAGE:",
        'ocr_fail': "No readable text found on the image.",
        'direct_img_prompt': "Image received. What would you like to do?",
        'btn_direct_pdf': "Convert to PDF",
        'btn_direct_ocr': "Extract Text (OCR)",
        'btn_cancel': "Cancel",
        'cancel_success': "Action cancelled.",
        'lang_changed': "Language updated successfully!",
        'tz_changed': "Timezone updated successfully!",
        'city_not_found': "City not found. Please enter a valid city name.",
        'weather_loading': "🌤️ Fetching weather data...",
        'weather_city_not_found': "Location not found. Please check spelling and try again.",
        'loc_prompt_multimatch': "📍 *MULTIPLE LOCATIONS FOUND*\n\nPlease select your exact location:",
        'btn_prayer_notif': "🔔 Prayer Time Notifications",
        'notif_menu_title': "🔔 *PRAYER TIME NOTIFICATIONS*",
        'notif_menu_desc': "Would you like to receive automated notifications when prayer time arrives?\nSelect your preference:",
        'notif_btn_on_time': "✅ Exactly on Time",
        'notif_btn_15m': "⏱️ 15 Minutes Before",
        'notif_btn_off': "🔕 Turn Off",
        'notif_saved': "Notification settings saved!",
        'notif_alert_title': "PRAYER TIME NOTIFICATION",
        'notif_entered': "time has arrived!",
        'notif_verse': "«Indeed, prayer has been decreed upon the believers a decree of specified times.» (An-Nisa, 103)",
        'friday_title': "BLESSED FRIDAY!",
        'friday_text': "Blessed Friday! Sending peace and blessings upon the Prophet (pbuh) and reciting Surah Al-Kahf is highly virtuous today. 🤲",
        'downloading': "Downloading media, please wait...",
        'uploading': "Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "An error occurred. Please try again.",
        'schedule_processing': "Generating lock-screen wallpaper...",
        'schedule_ready_caption': "Class Schedule (Lock Screen)",
        'doc_processing': "File received, processing...",
        'video_error': "An error occurred during video download.",
        'rate_limit_alert': "⚠️ Please do not click buttons too fast.",
        'adhkar_morning_btn': "🌅 Morning Adhkar",
        'adhkar_evening_btn': "🌇 Evening Adhkar",
        'adhkar_salawat_btn': "🤲 Salawat",
        'adhkar_morning_text': (
            "🌅 *MORNING ADHKAR (ARABIC TEXT & MEANING)*\n\n"
            "1️⃣ *Ayat al-Kursi (Surah Al-Baqarah, Ayah 255)*\n"
            "اللَّهُ لَا إِلَٰهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ ۚ لَا تَأْخُذُهُ سِنَةٌ وَلَا نَوْمٌ ۚ لَهُ مَا فِي السَّمَاوَاتِ وَمَا فِي الْأَرْضِ ۗ مَنْ ذَا الَّذِي يَشْفَعُ عِنْدَهُ إِلَّا بِإِذْنِهِ ۚ يَعْلَمُ مَا بَيْنَ أَيْدِيهِمْ وَمَا خَلْفَهُمْ ۖ وَلَا يُحِيطُونَ بِشَيْءٍ مِنْ عِلْمِهِ إِلَّا بِمَا شَاءَ ۚ وَسِعَ كُرْسِيُّهُ السَّمَاوَاتِ وَالْأَرْضَ ۖ وَلَا يَئُودُهُ حِفْظُهُمَا ۚ وَهُوَ الْعَلِيُّ الْعَظِيمُ\n\n"
            "2️⃣ *Surahs Al-Ikhlas, Al-Falaq, An-Nas (3 times each)*\n"
            "3️⃣ *Sayyid al-Istighfar (The Master Supplication for Forgiveness)*\n"
            "اللَّهُمَّ أَنْتَ رَبِّي لَا إِلَٰهَ إِلَّا أَنْتَ، خَلَقْتَنِي وَأَنَا عَبْدُكَ، وَأَنَا عَلَىٰ عَهْدِكَ وَوَعْدِكَ مَا اسْتَطَعْتُ، أَعُوذُ بِكَ مِنْ شَرِّ مَا صَنَعْتُ، أَبُوءُ لَكَ بِنِعْمَتِكَ عَلَيَّ، وَأَبُوءُ لَكَ بِذَنْبِي فَاغْفِرْ لِي، فَإِنَّهُ لَا يَغْفِرُ الذُّنُوبَ إِلَّا أَنْتَ"
        ),
        'adhkar_evening_text': (
            "🌇 *EVENING ADHKAR (ARABIC TEXT & MEANING)*\n\n"
            "1️⃣ *Ayat al-Kursi*\n"
            "2️⃣ *Surahs Al-Ikhlas, Al-Falaq, An-Nas (3 times)*\n"
            "3️⃣ *Sayyid al-Istighfar*"
        ),
        'adhkar_salawat_text': (
            "🤲 *AUTHENTIC SALAWAT & TRANSLATIONS*\n\n"
            "1️⃣ *Salawat Ibrahimiyyah (Prayer Salawat)*\n"
            "اللَّهُمَّ صَلِّ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ كَمَا صَلَّيْتَ عَلَى إِبْرَاهِيمَ وَعَلَى آلِ إِبْرَاهِيمَ إِنَّكَ حَمِيدٌ مَجِيدٌ، اللَّهُمَّ بَارِكْ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ كَمَا بَارَكْتَ عَلَى إِبْرَاهِيمَ وَعَلَى آلِ إِبْرَاهِيمَ إِنَّكَ حَمِيدٌ مَجِيدٌ"
        )
    }
}

def get_text(user_id, key, context=None) -> str:
    lang = get_user_lang(user_id, context)
    return TEXTS.get(lang, TEXTS['uz']).get(key, TEXTS['uz'].get(key, ''))

def get_reply_menu(user_id, context=None):
    lang = get_user_lang(user_id, context)
    t = TEXTS.get(lang, TEXTS['uz'])
    rows = [
        [KeyboardButton(t['btn_video']), KeyboardButton(t['btn_prayer'])],
        [KeyboardButton(t['btn_weather']), KeyboardButton(t['btn_pdf_hub'])],
        [KeyboardButton(t['btn_exam']), KeyboardButton(t['btn_schedule_img'])],
        [KeyboardButton(t['btn_pomodoro']), KeyboardButton(t['btn_translit'])],
        [KeyboardButton(t['btn_timezone_hub']), KeyboardButton(t['btn_lang'])],
        [KeyboardButton(t['btn_feedback']), KeyboardButton(t['btn_reset'])],
    ]
    if user_id in ADMIN_IDS:
        admin_btn_lbl = {
            'tr': "👑 Yönetici Paneli",
            'uz': "👑 Boshqaruv Paneli",
            'ru': "👑 Панель Управления",
            'en': "👑 Admin Panel"
        }.get(lang, "👑 Yönetici Paneli")
        rows.append([KeyboardButton(admin_btn_lbl)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# =====================================================================
# VİDEO & MEDYA İNDİRME MOTORU
# =====================================================================
SUPPORTED_PLATFORMS = [
    r'(?:instagram\.com|instagr\.am|threads\.net)',
    r'(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)',
    r'(?:facebook\.com|fb\.watch|fb\.gg|fb\.me|m\.facebook\.com)',
    r'(?:twitter\.com|x\.com)',
    r'(?:youtube\.com|youtu\.be)'
]

def is_supported_url(url: str) -> bool:
    return any(p.search(url) for p in RE_SUPPORTED_PLATFORMS)

class FileTooLargeError(Exception):
    def __init__(self, size_mb: float):
        self.size_mb = size_mb
        super().__init__(f"File size ({size_mb:.1f} MB) exceeds Telegram 50 MB limit.")

def extract_instagram_code(url: str) -> str:
    m = RE_INSTA_CODE.search(url)
    return m.group(1) if m else None

def parse_media_url_from_html(html_text: str):
    m = re.search(r'"video_url"\s*:\s*"([^"]+)"', html_text)
    if m:
        return html_lib.unescape(m.group(1).replace(r'\/', '/').replace(r'\u0026', '&')), True

    m = re.search(r'<meta[^>]+(?:property|name)=["\']og:video(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
    if m:
        return html_lib.unescape(m.group(1)), True

    m = re.search(r'<meta[^>]+(?:property|name)=["\']twitter:player:stream["\'][^>]+content=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
    if m:
        return html_lib.unescape(m.group(1)), True

    m = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
    if m:
        return html_lib.unescape(m.group(1)), True

    m = re.search(r'"display_url"\s*:\s*"([^"]+)"', html_text)
    if m:
        return html_lib.unescape(m.group(1).replace(r'\/', '/').replace(r'\u0026', '&')), False

    m = re.search(r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
    if m:
        return html_lib.unescape(m.group(1)), False

    return None, False

def download_direct_url(direct_url: str, output_path: str, max_bytes: int = 50 * 1024 * 1024):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Referer": "https://www.instagram.com/",
    }
    client = get_sync_http_client()
    with client.stream("GET", direct_url, headers=headers) as response:
            if response.status_code != 200:
                raise RuntimeError(f"HTTP stream basarisiz: {response.status_code}")
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise FileTooLargeError(int(content_length) / (1024 * 1024))
            downloaded = 0
            with open(output_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=65536):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise FileTooLargeError(downloaded / (1024 * 1024))
                    f.write(chunk)

def fallback_instagram_download(code: str, download_dir: str) -> dict:
    headers_embed = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-App-ID": "936619743392459",
    }
    headers_bot = {
        "User-Agent": "TelegramBot (like TwitterBot)",
        "Accept": "*/*",
    }

    candidate_endpoints = [
        (f"https://www.instagram.com/p/{code}/embed/captioned/", headers_embed),
        (f"https://instagramez.com/reel/{code}/", headers_bot),
        (f"https://www.ddinstagram.com/reel/{code}/", headers_bot),
        (f"https://instagramez.com/p/{code}/", headers_bot),
        (f"https://www.ddinstagram.com/p/{code}/", headers_bot),
    ]

    client = get_sync_http_client()
    for target_url, hdrs in candidate_endpoints:
            try:
                resp = client.get(target_url, headers=hdrs)
                if resp.status_code == 200 and resp.text:
                    media_url, is_video = parse_media_url_from_html(resp.text)
                    if media_url:
                        ext = ".mp4" if is_video else ".jpg"
                        out_file = os.path.join(download_dir, f"insta_{code}{ext}")
                        download_direct_url(media_url, out_file)

                        size_mb = os.path.getsize(out_file) / (1024 * 1024)
                        if size_mb > 49.5:
                            raise FileTooLargeError(size_mb)

                        return {
                            'path': out_file,
                            'title': f"Instagram @{code}",
                            'duration': 0,
                            'width': None,
                            'height': None,
                            'size_mb': size_mb,
                            'is_video': is_video,
                            'is_photo': not is_video
                        }
            except FileTooLargeError:
                raise
            except Exception:
                continue

    return None

def fallback_twitter_download(tweet_id: str, download_dir: str) -> dict:
    api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
    try:
        client = get_sync_http_client()
        resp = client.get(api_url)
        if resp.status_code == 200:
            data = resp.json()
            tweet = data.get("tweet", {})
            media = tweet.get("media", {})
            videos = media.get("videos", [])
            if videos:
                v_url = videos[0].get("url")
                if v_url:
                    out_file = os.path.join(download_dir, f"x_{tweet_id}.mp4")
                    download_direct_url(v_url, out_file)
                    size_mb = os.path.getsize(out_file) / (1024 * 1024)
                    if size_mb > 49.5:
                        raise FileTooLargeError(size_mb)
                    return {
                        'path': out_file,
                        'title': tweet.get("text", "X Video")[:50],
                        'duration': int(videos[0].get("duration", 0)),
                        'width': videos[0].get("width"),
                        'height': videos[0].get("height"),
                        'size_mb': size_mb,
                        'is_video': True,
                        'is_photo': False
                    }
    except FileTooLargeError:
        raise
    except Exception:
        pass
    return None

def _yt_dlp_download(url: str, download_dir: str, audio_only: bool = False) -> dict:
    out_tmpl = os.path.join(download_dir, 'media_%(id)s.%(ext)s')
    clean_url = url.strip()
    ydl_opts = {
        'outtmpl': out_tmpl,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'noplaylist': True,
        'socket_timeout': 25,
        'retries': 4,
        'max_filesize': 49 * 1024 * 1024,
    }
    if audio_only:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['extract_audio'] = True
    else:
        ydl_opts['format'] = 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'

    if 'instagram.com' in clean_url or 'instagr.am' in clean_url:
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate',
            'X-IG-App-ID': '936619743392459',
        }
        ydl_opts['extractor_args'] = {'instagram': {'app_id': ['936619743392459']}}
    elif 'youtube.com' in clean_url or 'youtu.be' in clean_url:
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        }
        ydl_opts['extractor_args'] = {'youtube': {'player_client': ['android', 'web']}}
    else:
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        }

    if shutil.which('ffmpeg'):
        ydl_opts['merge_output_format'] = 'mp4'

    cookie_path = os.environ.get("COOKIE_FILE", "cookies.txt")
    if os.path.exists(cookie_path):
        ydl_opts['cookiefile'] = cookie_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = (info.get('title') or 'Video').strip() if info else 'Video'
        duration = info.get('duration', 0) if info else 0
        width = info.get('width') if info else None
        height = info.get('height') if info else None

        valid_files = []
        for f in os.listdir(download_dir):
            full_p = os.path.join(download_dir, f)
            if os.path.isfile(full_p):
                ext = os.path.splitext(f)[1].lower()
                if ext not in ('.part', '.ytdl', '.temp') and os.path.getsize(full_p) > 1024:
                    valid_files.append(full_p)

        if not valid_files:
            raise RuntimeError("Dosya indirilemedi veya platform icerigi kisitladi.")

        valid_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
        chosen_file = valid_files[0]
        size_mb = os.path.getsize(chosen_file) / (1024 * 1024)

        if size_mb > 49.5:
            raise FileTooLargeError(size_mb)

        ext = os.path.splitext(chosen_file)[1].lower()
        is_video = ext in ('.mp4', '.mov', '.mkv', '.webm', '.avi')
        is_photo = ext in ('.jpg', '.jpeg', '.png', '.webp')

        return {
            'path': chosen_file,
            'title': title,
            'duration': duration,
            'width': width,
            'height': height,
            'size_mb': size_mb,
            'is_video': is_video,
            'is_photo': is_photo
        }

def download_media_sync(url: str, download_dir: str) -> dict:
    clean_url = url.strip()
    insta_code = extract_instagram_code(clean_url)
    if insta_code:
        clean_url = f"https://www.instagram.com/reel/{insta_code}/"

    try:
        return _yt_dlp_download(clean_url, download_dir)
    except FileTooLargeError:
        raise
    except Exception as e:
        print(f"[DOWNLOAD_WARNING] yt-dlp ile indirilemedi ({e}). Akilli yedek motoru devrede...")

        if insta_code:
            fallback_res = fallback_instagram_download(insta_code, download_dir)
            if fallback_res:
                return fallback_res

        m_x = re.search(r'(?:twitter\.com|x\.com)/(?:[^/]+/)?status/(\d+)', clean_url)
        if m_x:
            fallback_x = fallback_twitter_download(m_x.group(1), download_dir)
            if fallback_x:
                return fallback_x

        raise e

def download_audio_sync(url: str, download_dir: str) -> dict:
    clean_url = url.strip()
    insta_code = extract_instagram_code(clean_url)
    if insta_code:
        clean_url = f"https://www.instagram.com/reel/{insta_code}/"
    try:
        return _yt_dlp_download(clean_url, download_dir, audio_only=True)
    except Exception as e:
        add_system_log(f"Audio indirme hatasi: {e}", level="WARN")
        raise e

# =====================================================================
# DİNAMİK BOT KOMUTLARI
# =====================================================================
USER_COMMANDS_CACHE = {}

def format_system_logs_card() -> tuple:
    logs_list = list(SYSTEM_LOGS)
    if not logs_list:
        log_text = "Henüz kaydedilmiş bir hata/olay logu bulunmuyor."
    else:
        log_text = "\n".join(logs_list[-20:])

    card = (
        "📋 *NUN PROJECT // CANLI SİSTEM & HATA GÜNLÜĞÜ*\n"
        f"Kayıtlı Olay Sayısı: `{len(logs_list)}` (Hafıza sınırı: Son 40)\n\n"
        f"```{log_text}```\n\n"
        "💡 _Not: Beklenmedik hatalar ve arka plan işlemleri buraya anlık işlenir._"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Logları Yenile", callback_data="admin_view_logs")],
        [InlineKeyboardButton("🔙 Sistem Paneline Dön", callback_data="admin_hub_system")]
    ])
    return card, kb

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    card, kb = format_system_logs_card()
    await update.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)

async def update_user_bot_commands(context: ContextTypes.DEFAULT_TYPE, user_id: int, lang: str):
    is_admin = user_id in ADMIN_IDS
    cache_key = (lang, is_admin)
    if USER_COMMANDS_CACHE.get(user_id) == cache_key:
        return
    commands_map = {
        'uz': [
            BotCommand("start", "Botni ishga tushirish"),
            BotCommand("menu", "Asosiy menyu"),
            BotCommand("namoz", "Namoz & Ibodat markazi"),
            BotCommand("obhavo", "Ob-havo maʼlumoti"),
            BotCommand("pdf", "PDF & Hujjatlar"),
            BotCommand("imtihon", "Imtihonlar taymeri"),
            BotCommand("vaqt", "Vaqt & Joylashuv"),
            BotCommand("reset", "Botni yangilash & Tozalash"),
            BotCommand("cancel", "Bekor qilish"),
        ],
        'tr': [
            BotCommand("start", "Botu başlat"),
            BotCommand("menu", "Ana menü"),
            BotCommand("namaz", "Namaz & İbadet merkezi"),
            BotCommand("hava", "Hava durumu"),
            BotCommand("pdf", "PDF & Belge araçları"),
            BotCommand("sinav", "Sınav & Geri sayım"),
            BotCommand("saat", "Saat & Konum"),
            BotCommand("reset", "Botu yenile & Baştan başlat"),
            BotCommand("cancel", "İptal et"),
        ],
        'ru': [
            BotCommand("start", "Запустить бота"),
            BotCommand("menu", "Главное меню"),
            BotCommand("namaz", "Намаз и Ибадат"),
            BotCommand("weather", "Прогноз погоды"),
            BotCommand("pdf", "PDF и Документы"),
            BotCommand("exam", "Таймер экзаменов"),
            BotCommand("time", "Время и Геолокация"),
            BotCommand("reset", "Сброс и перезапуск бота"),
            BotCommand("cancel", "Отмена"),
        ],
        'en': [
            BotCommand("start", "Start the bot"),
            BotCommand("menu", "Main menu"),
            BotCommand("prayer", "Prayer & Worship hub"),
            BotCommand("weather", "Weather forecast"),
            BotCommand("pdf", "PDF & Documents"),
            BotCommand("exam", "Exam countdown"),
            BotCommand("time", "Time & Location"),
            BotCommand("reset", "Refresh & Reset bot"),
            BotCommand("cancel", "Cancel action"),
        ],
    }
    try:
        cmds = list(commands_map.get(lang, commands_map['uz']))
        if user_id in ADMIN_IDS:
            stats_desc = {
                'tr': "👑 Sahip & İstatistik Paneli",
                'uz': "👑 Admin & Statistika paneli",
                'ru': "👑 Панель статистики владельца",
                'en': "👑 Owner & Stats Panel"
            }.get(lang, "👑 Stats Panel")
            cmds.append(BotCommand("admin", "👑 Yönetici Kontrol Paneli"))
            cmds.append(BotCommand("stats", stats_desc))
            cmds.append(BotCommand("users", "👥 Kayıtlı Kullanıcılar Dizini"))
            cmds.append(BotCommand("find", "🔍 Kullanıcı Ara (ID / İsim)"))
            cmds.append(BotCommand("broadcast", "📢 Segmentli Toplu Duyuru"))
            cmds.append(BotCommand("backup", "💾 Veritabanı Yedeği Al"))
            cmds.append(BotCommand("restore", "📥 Yedekten Geri Yükle"))
            cmds.append(BotCommand("admins", "👑 Yönetici Kadrosu & Yetkiler"))
            cmds.append(BotCommand("logs", "📋 Sistem & Hata Günlüğü"))
            cmds.append(BotCommand("restart", "🔄 Botu Yeniden Başlat"))
        bot = getattr(context, 'bot', context)
        await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=user_id))
        USER_COMMANDS_CACHE[user_id] = cache_key
    except Exception:
        pass

# =====================================================================
# TELEGRAM TEMEL KOMUTLARI
# =====================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    track_user_activity(user)
    cleanup_user_temp_files(context, user_id)
    if str(user_id) not in USER_LANGS:
        tele_lang = user.language_code or 'uz'
        init_lang = 'tr' if tele_lang.startswith('tr') else ('ru' if tele_lang.startswith('ru') else ('en' if tele_lang.startswith('en') else 'uz'))
        save_user_lang(user_id, init_lang)
    u_lang = get_user_lang(user_id, context)
    await update_user_bot_commands(context, user_id, u_lang)
    await update.message.reply_text(get_text(user_id, 'welcome', context), reply_markup=get_reply_menu(user_id, context))

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    track_user_activity(user)
    cleanup_user_temp_files(context, user_id)
    await update.message.reply_text(get_text(user_id, 'menu_title', context), reply_markup=get_reply_menu(user_id, context))

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    track_user_activity(user)
    cleanup_user_temp_files(context, user_id)
    await update.message.reply_text(get_text(user_id, 'cancel_success', context), reply_markup=get_reply_menu(user_id, context))

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    curr_mid = update.message.message_id
    track_user_activity(user)
    factory_reset_user(user_id)
    cleanup_user_temp_files(context, user_id)
    context.user_data.clear()
    context.user_data['mode'] = 'auto'
    u_lang = get_user_lang(user_id, context)
    await update_user_bot_commands(context, user_id, u_lang)
    await wipe_chat_history(context.bot, chat_id, curr_mid, count=80)
    await context.bot.send_message(
        chat_id=chat_id,
        text=get_text(user_id, 'reset_success', context),
        parse_mode="Markdown",
        reply_markup=get_reply_menu(user_id, context)
    )

# =====================================================================
# SAHİP VE GELİŞTİRİCİ KONTROL PANELİ
# =====================================================================
USERS_CACHE_DATA = None
USERS_CACHE_TS = 0.0

def invalidate_users_cache():
    global USERS_CACHE_DATA, USERS_CACHE_TS
    USERS_CACHE_DATA = None
    USERS_CACHE_TS = 0.0

def get_all_registered_users() -> list:
    global USERS_CACHE_DATA, USERS_CACHE_TS
    now_t = time.time()
    if USERS_CACHE_DATA is not None and (now_t - USERS_CACHE_TS < 30.0):
        return USERS_CACHE_DATA

    all_uids = set()
    for d in (USER_LANGS, USER_TIMEZONES, USER_CITIES, USER_COORDS, USER_EXAMS, USER_REMINDERS, USER_TODOS, USER_PRAYER_NOTIFS, USER_FRIDAY_NOTIFS, USER_PROFILES):
        all_uids.update(str(k) for k in d.keys())
    
    for aid in ADMIN_IDS:
        all_uids.add(str(aid))

    users_list = []
    for uid_str in all_uids:
        try:
            uid = int(uid_str)
        except Exception:
            continue
        p = USER_PROFILES.get(uid_str, {})
        name = p.get('name') or f"Kullanıcı {uid}"
        username = p.get('username') or ""
        last_active = p.get('last_active') or "Kayıtlı (Pasif)"
        lang = USER_LANGS.get(uid_str, "uz")
        city = USER_CITIES.get(uid_str, "")
        tz = USER_TIMEZONES.get(uid_str)
        prayer_active = USER_PRAYER_NOTIFS.get(uid_str, {}).get("enabled", False)

        users_list.append({
            'id': uid,
            'name': name,
            'username': username,
            'last_active': last_active,
            'lang': lang,
            'city': city,
            'tz': tz,
            'prayer_active': prayer_active
        })

    def sort_key(u):
        la = u.get('last_active', '')
        if "Kayıtlı" in la or not la:
            return "0000-00-00 00:00:00"
        return la

    users_list.sort(key=sort_key, reverse=True)
    USERS_CACHE_DATA = users_list
    USERS_CACHE_TS = now_t
    return users_list

# DÜZELTME 1: Mobilde sade, şık ve ferah 3 satırlı ana kontrol paneli
def format_admin_stats_panel() -> tuple:
    all_users = get_all_registered_users()
    user_count = len(all_users)
    prayer_notif_count = sum(1 for cfg in USER_PRAYER_NOTIFS.values() if cfg.get("enabled"))
    friday_notif_count = len(USER_FRIDAY_NOTIFS)
    exam_count = sum(len(v) for v in USER_EXAMS.values())
    remind_count = sum(len(v) for v in USER_REMINDERS.values())
    todo_count = sum(len(v) for v in USER_TODOS.values())
    banned_count = len(BANNED_USERS)
    uptime_str = get_uptime_string()
    ram_mb = get_ram_usage_mb()
    disk_info = get_disk_usage_info()
    maint_lbl = "AÇIK 🔴" if is_maintenance_active() else "KAPALI 🟢"

    dau, wau, passive = calculate_activity_metrics()
    report = (
        f"👑 *NUN PROJECT // YÖNETİCİ KONTROL MERKEZİ*\n\n"
        f"📊 *KULLANICI VE AKTİFLİK ANALİZİ:*\n"
        f"  ▫️ Toplam Kayıtlı: `{user_count}` | Banlı: `{banned_count}`\n"
        f"  ▫️ Canlı Aktiflik: Bugün `{dau}` (DAU) | Bu Hafta `{wau}` (WAU)\n"
        f"  ▫️ Pasif Kullanıcılar: `{passive}` kişi\n"
        f"  ▫️ Vakit Bildirimi: `{prayer_notif_count}` | Cuma: `{friday_notif_count}`\n"
        f"  ▫️ Sınavlar: `{exam_count}` | Görevler (To-Do): `{todo_count}`\n\n"
        f"⚡ *SUNUCU SAĞLIĞI:*\n"
        f"  ▫️ Çalışma Süresi: `{uptime_str}`\n"
        f"  ▫️ Bellek (RAM): `{ram_mb:.1f} MB` | Disk: `{disk_info}`\n"
        f"  ▫️ Bakım Modu: `{maint_lbl}`\n\n"
        f"📱 _Yönetmek istediğiniz kategoriyi seçiniz:_"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"👥 Kullanıcı İşlemleri ({user_count})", callback_data="admin_hub_users"),
            InlineKeyboardButton("📢 Toplu Duyuru Merkezi", callback_data="admin_broadcast_prompt")
        ],
        [
            InlineKeyboardButton("⚙️ Sistem & Veritabanı", callback_data="admin_hub_system"),
            InlineKeyboardButton("🛡️ Güvenlik & Bakım", callback_data="admin_hub_security")
        ],
        [
            InlineKeyboardButton("🔄 Paneli Yenile", callback_data="stats_refresh"),
            InlineKeyboardButton("❌ Kapat", callback_data="admin_panel_close")
        ]
    ])
    return report, kb

def format_admin_users_hub() -> tuple:
    all_users = get_all_registered_users()
    banned_cnt = len(BANNED_USERS)
    text = (
        "👥 *NUN PROJECT // KULLANICI YÖNETİM MERKEZİ*\n\n"
        f"▫️ Kayıtlı Kullanıcı Sayısı: `{len(all_users)}`\n"
        f"▫️ Engellenen (Banlı) Üye: `{banned_cnt}`\n\n"
        "_Yapmak istediğiniz işlemi seçiniz:_"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👥 Kayıtlı Kullanıcılar Dizini ({len(all_users)})", callback_data="stats_users_page_0")],
        [InlineKeyboardButton("🔍 Kullanıcı Ara (ID / İsim)", callback_data="admin_search_prompt")],
        [
            InlineKeyboardButton("📊 Excel İndir (.xlsx)", callback_data="admin_export_excel"),
            InlineKeyboardButton("🏆 Modül İstatistikleri", callback_data="admin_module_stats")
        ],
        [InlineKeyboardButton(f"⛔ Engellenen (Banlı) Üyeler ({banned_cnt})", callback_data="admin_banned_list")],
        [InlineKeyboardButton("🔙 Ana Panele Dön", callback_data="stats_back_main")]
    ])
    return text, kb

def format_admin_system_hub() -> tuple:
    ram_mb = get_ram_usage_mb()
    disk_info = get_disk_usage_info()
    text = (
        "⚙️ *NUN PROJECT // SİSTEM & VERİTABANI YÖNETİMİ*\n\n"
        f"▫️ Bellek (RAM): `{ram_mb:.1f} MB`\n"
        f"▫️ Disk Durumu: `{disk_info}`\n"
        f"▫️ Kesintisiz Çalışma: `{get_uptime_string()}`\n\n"
        "_Veritabanı ve telemetri kontrolleri:_"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Veritabanı Yedeği Al (ZIP)", callback_data="admin_backup_download"),
            InlineKeyboardButton("📥 Yedekten Geri Yükle", callback_data="admin_restore_prompt")
        ],
        [
            InlineKeyboardButton("🩺 DB Sağlık Taraması & Onarım", callback_data="admin_db_health"),
            InlineKeyboardButton("📋 Canlı Sistem Logları", callback_data="admin_view_logs")
        ],
        [InlineKeyboardButton("🧹 Geçici Çöp Dosyaları Temizle", callback_data="admin_clean_disk")],
        [InlineKeyboardButton("🔙 Ana Panele Dön", callback_data="stats_back_main")]
    ])
    return text, kb

def format_admin_security_hub() -> tuple:
    maint_lbl = "Bakım Modu: AÇIK 🔴" if is_maintenance_active() else "Bakım Modu: KAPALI 🟢"
    maint_btn = "🚧 Bakımı Kapat 🟢" if is_maintenance_active() else "🚧 Bakımı Aç 🔴"
    text = (
        "🛡️ *NUN PROJECT // GÜVENLİK VE BAKIM KONTROLÜ*\n\n"
        f"▫️ Mevcut Durum: *{maint_lbl}*\n"
        f"▫️ Yetkili Yönetici Sayısı: `{len(ADMIN_IDS)}`\n\n"
        "_Güvenlik, yetki ve sunucu operasyonları:_"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Yönetici Kadrosu & Yetkiler", callback_data="admin_manage_panel")],
        [InlineKeyboardButton(maint_btn, callback_data="admin_toggle_maintenance")],
        [InlineKeyboardButton("🔄 Botu Yeniden Başlat (Reboot)", callback_data="admin_restart_prompt")],
        [InlineKeyboardButton("🔙 Ana Panele Dön", callback_data="stats_back_main")]
    ])
    return text, kb

# DÜZELTME 2: HTML modunda hatasız güvenli kullanıcı kartı
def format_user_detail_card(target_uid: int) -> tuple:
    uid_str = str(target_uid)
    p = USER_PROFILES.get(uid_str, {})
    name = p.get('name') or f"User {target_uid}"
    clean_name = html_lib.escape(name)
    uname = p.get('username') or ""
    uname_str = f"@{html_lib.escape(uname)}" if uname else "<i>(Yok)</i>"
    la = p.get('last_active') or "Bilinmiyor"
    lang_str = (USER_LANGS.get(uid_str) or "uz").upper()
    city_str = html_lib.escape(USER_CITIES.get(uid_str) or "Belirtilmedi")
    tz_val = USER_TIMEZONES.get(uid_str, 0)
    tz_str = f"UTC{'+' if tz_val >= 0 else ''}{tz_val}" if uid_str in USER_TIMEZONES else "Belirtilmedi"
    p_notif = USER_PRAYER_NOTIFS.get(uid_str, {}).get("enabled", False)
    p_status = "🔔 Açık" if p_notif else "🔕 Kapalı"

    last_act = p.get('last_action', 'Yok')
    act_labels = {
        'video': '🎬 Video İndirme', 'prayer': '🕌 Namaz Vakti',
        'weather': '🌤️ Hava Durumu', 'pdf_hub': '📄 PDF & OCR',
        'exam_todo': '🎓 Sınav & To-Do', 'pomodoro': '⏱️ Pomodoro',
        'translit': '🔤 Çeviri', 'adhkar': '📿 Zikir', 'hadith': '📖 Hadis'
    }
    last_act_str = act_labels.get(last_act, last_act.title()) if last_act != 'Yok' else "Henüz işlem yok"

    is_admin = target_uid in ADMIN_IDS
    is_banned = is_user_banned(target_uid)
    ban_info = get_user_ban_info(target_uid) if is_banned else None

    if is_admin:
        status_str = "👑 Yönetici"
    elif is_banned:
        b_mode = ban_info.get('mode', 'soft') if ban_info else 'soft'
        status_str = f"⛔ Engelli ({'🔴 Tam Men' if b_mode == 'hard' else '🟡 Standart Ban'})"
    elif p.get('bot_blocked'):
        status_str = "🚫 Botu Engelledi"
    else:
        status_str = "🟢 Aktif"

    card_lines = [
        "👤 <b>NUN PROJECT // KULLANICI YÖNETİM KARTI</b>\n",
        f"▫️ <b>İsim:</b> {clean_name}",
        f"▫️ <b>Kullanıcı Adı:</b> {uname_str}",
        f"▫️ <b>Telegram ID:</b> <code>{target_uid}</code>",
        f"▫️ <b>Şehir:</b> <code>{city_str}</code> | <b>Dil:</b> <code>{lang_str}</code> | <b>Saat:</b> <code>{tz_str}</code>",
        f"▫️ <b>Ezan Bildirimi:</b> {p_status}",
        f"▫️ <b>Son Aktiflik:</b> <code>{la[:19]}</code>",
        f"▫️ <b>Son Yapılan İşlem:</b> {last_act_str}",
        f"▫️ <b>Durum:</b> {status_str}",
    ]
    if is_banned and ban_info:
        card_lines.append(f"▫️ <b>Ban Sebebi:</b> <i>{html_lib.escape(ban_info.get('reason', ''))}</i>")
        card_lines.append(f"▫️ <b>Ban Tarihi:</b> <code>{ban_info.get('timestamp', '')}</code>")

    card_text = "\n".join(card_lines)

    btns = []
    btns.append([
        InlineKeyboardButton("✉️ Bot İçi DM", callback_data=f"admin_dm_start_{target_uid}"),
        InlineKeyboardButton("⚡ 1:1 Sohbet Aç", callback_data=f"admin_direct_invite_{target_uid}")
    ])
    if is_banned:
        btns.append([InlineKeyboardButton("✅ Engeli Kaldır (Unban)", callback_data=f"admin_unban_{target_uid}")])
    else:
        btns.append([InlineKeyboardButton("⛔ Kullanıcıyı Engelle (Ban)", callback_data=f"admin_ban_choose_{target_uid}")])

    if is_admin:
        if len(ADMIN_IDS) > 1:
            btns.append([InlineKeyboardButton("🔻 Yöneticilikten Çıkar", callback_data=f"admin_demote_{target_uid}")])
    else:
        btns.append([InlineKeyboardButton("👑 Yönetici Yetkisi Ver", callback_data=f"admin_promote_{target_uid}")])

    btns.append([InlineKeyboardButton("🔄 Canlı Bilgileri Çek (get_chat)", callback_data=f"admin_fetch_chat_{target_uid}")])
    btns.append([InlineKeyboardButton("🔙 Kullanıcılar Listesine Dön", callback_data="stats_users_page_0")])

    return card_text, InlineKeyboardMarkup(btns)

def format_user_ban_options_card(target_uid: int) -> tuple:
    p = USER_PROFILES.get(str(target_uid), {})
    name = html_lib.escape(p.get('name') or f"User {target_uid}")
    text = (
        f"⛔ <b>KULLANICI ENGELLEME SEÇENEKLERİ</b>\n\n"
        f"Hedef Kullanıcı: <b>{name}</b> (<code>{target_uid}</code>)\n\n"
        f"Lütfen uygulamak istediğiniz engelleme türünü seçiniz:\n\n"
        f"1️⃣ <b>🟡 Standart Ban (Botta Bırak):</b>\n"
        f"Kullanıcı engellenir ve mesajlarına yanıt verilmez. Ancak ayarları korunur.\n\n"
        f"2️⃣ <b>🔴 Tam Men Et (Bottan Tamamen At):</b>\n"
        f"Kullanıcının telefonundaki bot menüsü silinir, bildirimleri tamamen kaldırılır."
    )
    btns = [
        [InlineKeyboardButton("🟡 Standart Ban (Botta Bırak)", callback_data=f"admin_ban_soft_prompt_{target_uid}")],
        [InlineKeyboardButton("🔴 Tam Men Et (Bottan At)", callback_data=f"admin_ban_hard_prompt_{target_uid}")],
        [
            InlineKeyboardButton("⚡ Hızlı Standart Ban", callback_data=f"admin_ban_soft_quick_{target_uid}"),
            InlineKeyboardButton("⚡ Hızlı Tam Men", callback_data=f"admin_ban_hard_quick_{target_uid}")
        ],
        [InlineKeyboardButton("❌ İptal / Kullanıcıya Dön", callback_data=f"admin_user_view_{target_uid}")]
    ]
    return text, InlineKeyboardMarkup(btns)

def format_users_directory_page(page: int = 0, page_size: int = 6) -> tuple:
    all_users = get_all_registered_users()
    total_users = len(all_users)
    total_pages = max(1, (total_users + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_users)
    page_users = all_users[start_idx:end_idx]

    lines = [
        "👥 <b>NUN PROJECT // KAYITLI KULLANICILAR DİZİNİ</b>",
        f"Toplam: <code>{total_users}</code> kullanıcı | Sayfa: <code>{page + 1}/{total_pages}</code>\n",
        "<i>İşlem yapmak istediğiniz kullanıcının butonuna dokunun:</i>\n"
    ]

    user_buttons = []
    for idx, u in enumerate(page_users, start=start_idx + 1):
        uid = u['id']
        name_clean = html_lib.escape(u['name'])
        uname_str = f"(@{html_lib.escape(u['username'])})" if u['username'] else ""
        la = u['last_active'][:16] if u['last_active'] else "Pasif"
        b_tag = ""
        if uid in ADMIN_IDS:
            b_tag = " [👑]"
        elif is_user_banned(uid):
            b_info = get_user_ban_info(uid)
            b_tag = " [🔴]" if (b_info and b_info.get('mode') == 'hard') else " [🟡]"

        lines.append(
            f"<b>{idx}.</b> {name_clean} {uname_str}{b_tag}\n"
            f"   🆔 ID: <code>{uid}</code> | Son: <code>{la}</code>"
        )

        btn_label = f"👤 {idx}. {(u['name'] or str(uid))[:15]} {uname_str[:12]}"
        user_buttons.append([InlineKeyboardButton(btn_label.strip(), callback_data=f"admin_user_view_{uid}")])

    lines.append("\n💡 <i>[👑] Yönetici | [🟡] Standart Ban | [🔴] Tam Men</i>")

    action_buttons = list(user_buttons)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Önceki", callback_data=f"stats_users_page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="cal_ignore"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Sonraki ▶️", callback_data=f"stats_users_page_{page + 1}"))

    if nav_row:
        action_buttons.append(nav_row)

    action_buttons.append([
        InlineKeyboardButton("🔍 Kullanıcı Ara", callback_data="admin_search_prompt"),
        InlineKeyboardButton("🔄 Eşitle & Güncelle", callback_data="admin_sync_users_prompt"),
        InlineKeyboardButton("🔄 Yenile", callback_data=f"stats_users_page_{page}")
    ])
    action_buttons.append([InlineKeyboardButton("🔙 Kullanıcı Merkezine Dön", callback_data="admin_hub_users")])

    return "\n".join(lines), InlineKeyboardMarkup(action_buttons)

def format_search_results_card(matches: list, query_str: str) -> tuple:
    if not matches:
        return f"🔍 <b>ARAMA SONUCU:</b>\n\n<code>{html_lib.escape(query_str)}</code> için sonuç bulunamadı.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ana Panele Dön", callback_data="stats_back_main")]])

    lines = [
        f"🔍 <b>KULLANICI ARAMA SONUÇLARI</b> (<code>{len(matches)}</code> eşleşme)",
        f"Aranan: <code>{html_lib.escape(query_str)}</code>\n"
    ]
    btns = []
    for idx, u in enumerate(matches[:5], start=1):
        uid = u['id']
        name_clean = html_lib.escape(u['name'])
        uname_str = f"@{html_lib.escape(u['username'])}" if u['username'] else "<i>(Yok)</i>"
        la = u['last_active'][:16] if u['last_active'] else "Pasif"
        city_str = html_lib.escape(u['city'] or "Belirtilmedi")
        p_status = "🔔 Açık" if u['prayer_active'] else "🔕 Kapalı"

        lines.append(
            f"<b>{idx}.</b> {name_clean} — {uname_str}\n"
            f"   🆔 ID: <code>{uid}</code> | Şehir: <code>{city_str}</code>\n"
            f"   🕒 Son Aktif: <code>{la}</code> | Ezan: {p_status}\n"
            f"   💬 Hızlı DM: <code>/msg_{uid}</code>\n"
        )
        row_btns = [
            InlineKeyboardButton(f"⚡ 1:1 Sohbet Başlat", callback_data=f"admin_direct_invite_{uid}"),
            InlineKeyboardButton(f"✉️ Bot İçi DM", callback_data=f"admin_dm_start_{uid}")
        ]
        btns.append(row_btns)

    btns.append([InlineKeyboardButton("🔙 Ana Panele Dön", callback_data="stats_back_main")])
    return "\n".join(lines), InlineKeyboardMarkup(btns)

async def send_admin_dm_to_user(bot, admin_id: int, target_uid: int, text: str, reply_to_message=None):
    clean_text = text.strip()
    if not clean_text:
        return False, "Mesaj metni boş olamaz."

    target_card = (
        "📩 <b>NUN PROJECT // YÖNETİCİ MESAJI</b>\n\n"
        f"{html_lib.escape(clean_text)}\n\n"
        "──────────────────────────────\n"
        "💬 <i>Bu mesaja doğrudan yanıt (reply) vererek yöneticiye yazabilirsiniz.</i>"
    )
    try:
        await bot.send_message(chat_id=target_uid, text=target_card, parse_mode="HTML")
        p = USER_PROFILES.get(str(target_uid), {})
        nm = html_lib.escape(p.get('name') or f"User {target_uid}")
        success_msg = f"✅ <b>Mesaj başarıyla iletildi!</b>\n👤 Alıcı: {nm} (<code>{target_uid}</code>)\n\n📝 <b>İletilen Mesaj:</b>\n<i>{html_lib.escape(clean_text)}</i>"
        if reply_to_message:
            await reply_to_message.reply_text(success_msg, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=admin_id, text=success_msg, parse_mode="HTML")
        return True, "İletildi"
    except Exception as e:
        err_msg = f"❌ <b>Mesaj iletilemedi!</b> (ID: <code>{target_uid}</code>)\nNedeni: <code>{html_lib.escape(str(e))}</code>\n<i>(Kullanıcı botu engellemiş olabilir)</i>"
        if reply_to_message:
            await reply_to_message.reply_text(err_msg, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=admin_id, text=err_msg, parse_mode="HTML")
        return False, str(e)

def format_broadcast_audience_menu() -> tuple:
    all_users = get_all_registered_users()
    total = len(all_users)
    tr_cnt = sum(1 for u in all_users if (u.get('lang') or 'uz') == 'tr')
    uz_cnt = sum(1 for u in all_users if (u.get('lang') or 'uz') == 'uz')
    ru_cnt = sum(1 for u in all_users if (u.get('lang') or 'uz') == 'ru')
    en_cnt = sum(1 for u in all_users if (u.get('lang') or 'uz') == 'en')
    prayer_cnt = sum(1 for u in all_users if u.get('prayer_active'))

    msg = (
        f"📢 *SEGMENTLİ DUYURU GÖNDERİMİ*\n\n"
        f"Lütfen duyurunun iletileceği hedef kitleyi seçiniz:\n\n"
        f"▫️ 🌍 Tüm Kullanıcılar: `{total}` kişi\n"
        f"▫️ 🇹🇷 Yalnızca Türkçe: `{tr_cnt}` kişi\n"
        f"▫️ 🇺🇿 Yalnızca Özbekçe: `{uz_cnt}` kişi\n"
        f"▫️ 🇷🇺 Yalnızca Rusça: `{ru_cnt}` kişi\n"
        f"▫️ 🇬🇧 Yalnızca İngilizce: `{en_cnt}` kişi\n"
        f"▫️ 🕌 Ezan Bildirimi Açık Olanlar: `{prayer_cnt}` kişi"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🌍 Tüm Kullanıcılar ({total})", callback_data="admin_bc_target_all")],
        [InlineKeyboardButton(f"🇹🇷 Türkçe ({tr_cnt})", callback_data="admin_bc_target_tr"), InlineKeyboardButton(f"🇺🇿 Özbekçe ({uz_cnt})", callback_data="admin_bc_target_uz")],
        [InlineKeyboardButton(f"🇷🇺 Rusça ({ru_cnt})", callback_data="admin_bc_target_ru"), InlineKeyboardButton(f"🇬🇧 İngilizce ({en_cnt})", callback_data="admin_bc_target_en")],
        [InlineKeyboardButton(f"🕌 Ezan Bildirimi Açık Olanlar ({prayer_cnt})", callback_data="admin_bc_target_prayer")],
        [InlineKeyboardButton("🔙 Ana Panele Dön", callback_data="stats_back_main")]
    ])
    return msg, kb

async def run_segmented_broadcast(bot, admin_id: int, text: str, target_segment: str = "all", reply_to_message=None):
    all_users = get_all_registered_users()
    if target_segment in ("tr", "uz", "ru", "en"):
        recipients = [u for u in all_users if (u.get("lang") or "uz") == target_segment]
        seg_name = {"tr": "Yalnızca Türkçe (TR)", "uz": "Yalnızca Özbekçe (UZ)", "ru": "Yalnızca Rusça (RU)", "en": "Yalnızca İngilizce (EN)"}.get(target_segment, target_segment)
    elif target_segment == "prayer":
        recipients = [u for u in all_users if u.get("prayer_active")]
        seg_name = "Sadece Ezan Bildirimi Açık Olanlar"
    else:
        recipients = all_users
        seg_name = "Tüm Kayıtlı Kullanıcılar (Genel)"

    global BROADCAST_ABORT_FLAG
    BROADCAST_ABORT_FLAG = False
    total = len(recipients)
    kb_abort = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Gönderimi Acil Durdur", callback_data="admin_abort_broadcast")]])
    status_msg = await bot.send_message(
        chat_id=admin_id,
        text=f"⏳ *{seg_name}* segmentine duyuru iletiliyor...\nHedef: `{total}` kullanıcı.",
        reply_markup=kb_abort,
        parse_mode="Markdown"
    )
    success_count = 0
    fail_count = 0
    b_card = (
        f"📢 *NUN PROJECT // RESMİ DUYURU*\n\n"
        f"{text}\n\n"
        f"──────────────────────────────\n"
        f"_Nun Bot Yönetimi_"
    )
    for u in recipients:
        uid = u['id']
        if is_user_banned(uid) or u.get('bot_blocked'):
            continue
        if BROADCAST_ABORT_FLAG:
            break
        try:
            await bot.send_message(chat_id=uid, text=b_card, parse_mode="Markdown")
            success_count += 1
            await asyncio.sleep(0.04)
        except RetryAfter as e:
            add_system_log(f"Telegram FloodControl (RetryAfter {e.retry_after}s) beklemede...")
            await asyncio.sleep(e.retry_after + 0.5)
            try:
                await bot.send_message(chat_id=uid, text=b_card, parse_mode="Markdown")
                success_count += 1
            except Exception:
                fail_count += 1
        except Exception as e_send:
            fail_count += 1
            if "blocked by the user" in str(e_send).lower() or "user is deactivated" in str(e_send).lower():
                mark_user_blocked(uid)

    aborted_note = "\n🛑 *YÖNETİCİ TARAFINDAN ACİL DURDURULDU!*\n" if BROADCAST_ABORT_FLAG else ""
    result_card = (
        f"📢 *DUYURU GÖNDERİM RAPORU*{aborted_note}\n"
        f"🎯 Hedef Segment: *{seg_name}*\n"
        f"👥 Toplam Hedef: `{total}`\n"
        f"✅ Başarıyla İletilen: `{success_count}`\n"
        f"❌ Ulaşılamayan / İptal: `{total - success_count}`\n\n"
        f"📝 *İletilen Metin:*\n_{safe_md(text)}_"
    )
    try:
        await status_msg.edit_text(result_card, parse_mode="Markdown")
    except Exception:
        await bot.send_message(chat_id=admin_id, text=result_card, parse_mode="Markdown")

def format_admins_panel() -> tuple:
    lines = [
        "👑 *NUN PROJECT // YÖNETİCİ KADROSU & YETKİLENDİRME*",
        f"Toplam Yetkili Sayısı: `{len(ADMIN_IDS)}`\n"
    ]
    btns = []
    for idx, aid in enumerate(sorted(ADMIN_IDS), start=1):
        p = USER_PROFILES.get(str(aid), {})
        nm = safe_md(p.get('name') or f"Admin {aid}")
        un = f"(@{p.get('username')})" if p.get('username') else ""
        lines.append(f"*{idx}.* {nm} {un}\n   🆔 ID: `{aid}`")
        if len(ADMIN_IDS) > 1:
            btns.append([InlineKeyboardButton(f"➖ Yetkisini Al: {nm[:12]}", callback_data=f"admin_demote_{aid}")])

    btns.append([InlineKeyboardButton("➕ Yeni Yönetici Ekle (ID ile)", callback_data="admin_add_prompt")])
    btns.append([InlineKeyboardButton("🔙 Güvenlik Paneline Dön", callback_data="admin_hub_security")])

    return "\n".join(lines), InlineKeyboardMarkup(btns)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    track_user_activity(user)
    if user_id not in ADMIN_IDS:
        return
    report, kb = format_admin_stats_panel()
    await update.message.reply_text(report, parse_mode="Markdown", reply_markup=kb, disable_web_page_preview=True)

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    track_user_activity(user)
    if user_id not in ADMIN_IDS:
        return
    text, kb = format_users_directory_page(0)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

async def admin_msg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        await update.message.reply_text("ℹ️ *Kullanım:* `/msg <Kullanıcı_ID> <Mesajınız>`", parse_mode="Markdown")
        return
    if not args[0].isdigit():
        await update.message.reply_text("⚠️ Geçersiz Kullanıcı ID. Sayısal Telegram ID giriniz.")
        return
    target_uid = int(args[0])
    if len(args) < 2:
        context.user_data['admin_dm_target'] = target_uid
        p = USER_PROFILES.get(str(target_uid), {})
        nm = safe_md(p.get('name') or f"User {target_uid}")
        await update.message.reply_text(f"✍️ *{nm}* (`{target_uid}`) kullanıcısına mesajınızı yazınız:", parse_mode="Markdown")
        return
    msg_text = " ".join(args[1:])
    await send_admin_dm_to_user(context.bot, user_id, target_uid, msg_text, update.message)

async def admin_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        msg, kb = format_broadcast_audience_menu()
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
        return
    broadcast_text = " ".join(args)
    await run_segmented_broadcast(context.bot, user_id, broadcast_text, "all", update.message)

async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    text, kb = format_admins_panel()
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    args = context.args
    if not args or not args[0].isdigit():
        context.user_data['admin_add_mode'] = True
        await update.message.reply_text("👑 *YENİ YÖNETİCİ EKLEME*\n\nLütfen Telegram ID'sini yazıp gönderin:", parse_mode="Markdown")
        return
    new_aid = int(args[0])
    add_admin_id(new_aid)
    u_lang = USER_LANGS.get(str(new_aid), 'tr')
    try:
        await update_user_bot_commands(context, new_aid, u_lang)
    except Exception:
        pass
    await update.message.reply_text(f"✅ `{new_aid}` başarıyla yönetici kadrosuna eklendi!", parse_mode="Markdown")

async def del_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("ℹ️ *Kullanım:* `/deladmin <Kullanıcı_ID>`", parse_mode="Markdown")
        return
    rem_aid = int(args[0])
    ok, msg = remove_admin_id(rem_aid)
    await update.message.reply_text(f"{'✅' if ok else '⚠️'} {msg}", parse_mode="Markdown")

async def sync_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    status_msg = await update.message.reply_text("⏳ Kullanıcı profilleri Telegram sunucularından taranıyor...")
    all_users = get_all_registered_users()
    up_cnt = 0
    for u in all_users:
        uid = u['id']
        uid_str = str(uid)
        try:
            chat = await context.bot.get_chat(chat_id=uid)
            c_name = (f"{chat.first_name or ''} {chat.last_name or ''}").strip() or f"User {uid}"
            c_uname = chat.username or ""
            p = USER_PROFILES.get(uid_str, {})
            p['id'] = uid
            p['name'] = c_name
            p['username'] = c_uname.lstrip('@')
            USER_PROFILES[uid_str] = p
            up_cnt += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    save_json(USER_PROFILES_FILE, USER_PROFILES)
    invalidate_users_cache()
    await status_msg.edit_text(f"✅ Kullanıcılar eşitlendi! ({up_cnt} profil güncellendi)")

async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("ℹ️ *Kullanım:* `/adduser <Kullanıcı_ID>`", parse_mode="Markdown")
        return
    new_uid = int(args[0])
    uid_str = str(new_uid)
    try:
        chat = await context.bot.get_chat(chat_id=new_uid)
        c_name = (f"{chat.first_name or ''} {chat.last_name or ''}").strip() or f"User {new_uid}"
        c_uname = chat.username or ""
    except Exception:
        c_name = f"User {new_uid}"
        c_uname = ""
    p = USER_PROFILES.get(uid_str, {})
    p['id'] = new_uid
    p['name'] = c_name
    p['username'] = c_uname.lstrip('@')
    p['last_active'] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    USER_PROFILES[uid_str] = p
    if uid_str not in USER_LANGS:
        USER_LANGS[uid_str] = "uz"
        save_json(LANG_FILE, USER_LANGS)
    save_json(USER_PROFILES_FILE, USER_PROFILES)
    invalidate_users_cache()
    await update.message.reply_text(f"✅ Kullanıcı eklendi: {safe_md(c_name)} (`{new_uid}`)", parse_mode="Markdown")

async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        context.user_data['admin_search_mode'] = True
        await update.message.reply_text("🔍 *KULLANICI ARAMA*\n\nID veya isim yazınız:", parse_mode="Markdown")
        return
    q_str = " ".join(args)
    matches = search_registered_users(q_str)
    card_text, kb_search = format_search_results_card(matches, q_str)
    await update.message.reply_text(card_text, parse_mode="HTML", reply_markup=kb_search, disable_web_page_preview=True)

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    rep_msg = update.message.reply_to_message
    if rep_msg and rep_msg.document and rep_msg.document.file_name and rep_msg.document.file_name.endswith(".zip"):
        status_dl = await update.message.reply_text("⏳ Yedek dosyası indiriliyor...")
        try:
            file_obj = await rep_msg.document.get_file()
            tmp_z = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            tmp_z.close()
            await file_obj.download_to_drive(tmp_z.name)
            ok, res_msg = restore_database_from_zip(tmp_z.name)
            os.remove(tmp_z.name)
            await status_dl.delete()
            await update.message.reply_text(f"{'✅' if ok else '❌'} {res_msg}")
        except Exception as e:
            await update.message.reply_text(f"❌ Hata: {e}")
        return

    context.user_data['admin_restore_mode'] = True
    await update.message.reply_text("📥 Lütfen yedek ZIP dosyasını belge olarak gönderin:")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    kb_reboot = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Evet, Botu Yeniden Başlat", callback_data="admin_confirm_restart")],
        [InlineKeyboardButton("❌ İptal", callback_data="cancel_action")]
    ])
    await update.message.reply_text("⚠️ Bot yeniden başlatılacaktır. Onaylıyor musunuz?", parse_mode="Markdown", reply_markup=kb_reboot)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    status_wait = await update.message.reply_text("⏳ Yedek alınıyor...")
    ok = await send_backup_to_admins(context.bot, trigger_type="Komut Talebi (/backup)")
    try:
        await status_wait.delete()
    except Exception:
        pass
    await update.message.reply_text("✅ Yedek yöneticilere gönderildi." if ok else "❌ Yedek alınamadı.")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("ℹ️ *Kullanım:* `/ban <ID> [soft|hard] [Sebep]`", parse_mode="Markdown")
        return
    target_uid = int(args[0])
    if target_uid in ADMIN_IDS:
        await update.message.reply_text("⚠️ Yöneticiler engellenemez.")
        return
    mode = "soft"
    reason = "Kural ihlali"
    if len(args) > 1 and args[1].lower() in ("hard", "tam", "at"):
        mode = "hard"
        reason = " ".join(args[2:]) if len(args) > 2 else "Kural ihlali (Tam Men)"
    else:
        reason = " ".join(args[1:]) if len(args) > 1 else "Kural ihlali"
    if mode == "hard":
        await execute_hard_ban(context.bot, target_uid, reason)
        await update.message.reply_text(f"🔴 `{target_uid}` tamamen men edildi.")
    else:
        await execute_soft_ban(context.bot, target_uid, reason)
        await update.message.reply_text(f"🟡 `{target_uid}` standart banlandı.")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("ℹ️ *Kullanım:* `/unban <ID>`", parse_mode="Markdown")
        return
    target_uid = int(args[0])
    ok = unban_user(target_uid)
    await update.message.reply_text("✅ Engel kaldırıldı." if ok else "⚠️ Bu kullanıcı banlı değil.")

async def banned_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    if not BANNED_USERS:
        await update.message.reply_text("Engellenmiş kullanıcı bulunmuyor.")
        return
    b_lines = ["⛔ *ENGELLENMİŞ KULLANICILAR:*\n"]
    for b_uid, b_info in list(BANNED_USERS.items()):
        p = USER_PROFILES.get(b_uid, {})
        nm = safe_md(p.get('name') or f"User {b_uid}")
        b_lines.append(f"▫️ {nm} (`{b_uid}`) — _{safe_md(b_info.get('reason'))}_")
    await update.message.reply_text("\n".join(b_lines), parse_mode="Markdown")

async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    new_st = toggle_maintenance_mode()
    st_text = "AÇILDI 🔴 (Yalnızca yöneticiler)" if new_st else "KAPATILDI 🟢 (Herkes erişebilir)"
    await update.message.reply_text(f"🚧 *Bakım Modu:* {st_text}", parse_mode="Markdown")

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cleanup_user_temp_files(context, user_id)
    context.user_data['mode'] = 'feedback_input'
    await update.message.reply_text(
        get_text(user_id, 'prompt_feedback', context),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
    )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    track_user_activity(user)
    if is_rate_limited(user_id, 0.5):
        return

    loc = update.message.location
    if not loc:
        return
    tz_offset = coords_to_tz_offset(loc.latitude, loc.longitude)
    save_user_timezone(user_id, tz_offset, locked=True)
    cleanup_user_temp_files(context, user_id)
    
    user_now = get_user_now(user_id, context)
    now_str = user_now.strftime("%H:%M")
    u_lang = get_user_lang(user_id, context)
    t = TEXTS.get(u_lang, TEXTS['uz'])
    tz_sign = "+" if tz_offset >= 0 else ""
    
    loc_lbl = "GPS Joylashuv" if u_lang=='uz' else ("GPS Konumu" if u_lang=='tr' else ("GPS Локация" if u_lang=='ru' else "GPS Location"))
    timings, d_name, dt_s, h_s, src = await fetch_prayer_times_by_coord(
        loc.latitude, loc.longitude, 0.0, loc_lbl, f"{loc.latitude:.3f}, {loc.longitude:.3f}", "", user_id=user_id
    )
    
    card = (
        f"📍 *{t['tz_loc_detected']}*\n\n"
        f"🕒 {t['btn_timezone']}: `UTC{tz_sign}{tz_offset}`\n"
        f"⏰ {t['current_time_lbl']}: `{now_str}`\n\n"
        f"_{t['tz_synced_hint']}_"
    )
    await update.message.reply_text(card, parse_mode="Markdown", reply_markup=get_reply_menu(user_id, context))
    
    if timings:
        p_card = format_prayer_card(f"{loc_lbl} ({loc.latitude:.3f}, {loc.longitude:.3f})", timings, dt_s, h_s, src, u_lang, user_now=user_now, user_id=user_id)
        kb = get_prayer_hub_keyboard(user_id, u_lang)
        await update.message.reply_text(p_card, parse_mode="Markdown", reply_markup=kb)

# =====================================================================
# CALLBACK QUERY İŞLEYİCİSİ (DÜZELTME 1 & DÜZELTME 2 ENTEGRE EDİLDİ)
# =====================================================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    user_id = user.id
    track_user_activity(user)

    if is_user_banned(user_id):
        try:
            await query.answer("⛔ Erişiminiz kısıtlanmıştır.", show_alert=True)
        except Exception:
            pass
        return

    if is_maintenance_active() and user_id not in ADMIN_IDS:
        try:
            await query.answer(get_text(user_id, 'maintenance_msg', context), show_alert=True)
        except Exception:
            pass
        return

    if is_rate_limited(user_id, 0.5):
        try:
            await query.answer(text=get_text(user_id, 'rate_limit_alert', context), show_alert=False)
        except Exception:
            pass
        return

    answered = False
    async def safe_answer(text=None, show_alert=False):
        nonlocal answered
        if not answered:
            try:
                if text:
                    await query.answer(text=text, show_alert=show_alert)
                else:
                    await query.answer()
                answered = True
            except Exception:
                pass

    data = query.data
    user_lang = get_user_lang(user_id, context)
    t = TEXTS.get(user_lang, TEXTS['uz'])

    # DÜZELTME 1: Admin Mobil Kategori Merkezleri
    if data == "admin_module_stats":
        if user_id in ADMIN_IDS:
            await safe_answer()
            text_ms, kb_ms = format_module_stats_card()
            await safe_edit_text_markup(query.message, text_ms, reply_markup=kb_ms, parse_mode="HTML")
        return

    if data == "admin_hub_users":
        if user_id in ADMIN_IDS:
            await safe_answer()
            text, kb = format_admin_users_hub()
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "admin_hub_system":
        if user_id in ADMIN_IDS:
            await safe_answer()
            text, kb = format_admin_system_hub()
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "admin_hub_security":
        if user_id in ADMIN_IDS:
            await safe_answer()
            text, kb = format_admin_security_hub()
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "admin_manage_panel":
        if user_id in ADMIN_IDS:
            text, kb = format_admins_panel()
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "admin_add_prompt":
        if user_id in ADMIN_IDS:
            context.user_data['admin_add_mode'] = True
            await query.message.reply_text(
                "👑 *YENİ YÖNETİCİ EKLEME*\n\nLütfen Telegram ID'sini yazınız:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
            )
        return

    if data.startswith("admin_promote_"):
        if user_id in ADMIN_IDS:
            target_aid = int(data.replace("admin_promote_", "", 1))
            add_admin_id(target_aid)
            u_lang = USER_LANGS.get(str(target_aid), 'tr')
            try:
                await update_user_bot_commands(context, target_aid, u_lang)
            except Exception:
                pass
            await safe_answer("👑 Yönetici kadrosuna eklendi!", show_alert=True)
            card_text, kb = format_user_detail_card(target_aid)
            await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("admin_demote_"):
        if user_id in ADMIN_IDS:
            target_aid = int(data.replace("admin_demote_", "", 1))
            ok, msg = remove_admin_id(target_aid)
            if ok:
                u_lang = USER_LANGS.get(str(target_aid), 'tr')
                try:
                    await update_user_bot_commands(context, target_aid, u_lang)
                except Exception:
                    pass
                text, kb = format_admins_panel()
                await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="Markdown")
            else:
                await query.answer(msg, show_alert=True)
        return

    # DÜZELTME 2: 1:1 Sohbet Davetini HTML ve hatasız gönderme
    if data.startswith("admin_direct_invite_"):
        if user_id in ADMIN_IDS:
            target_uid = int(data.replace("admin_direct_invite_", "", 1))
            admin_user = query.from_user
            admin_raw_name = (f"{admin_user.first_name or ''} {admin_user.last_name or ''}").strip() or "Yönetici"
            clean_admin_name = html_lib.escape(admin_raw_name)
            admin_uname = admin_user.username

            p = USER_PROFILES.get(str(target_uid), {})
            t_raw_name = p.get('name') or f"User {target_uid}"
            clean_target_name = html_lib.escape(t_raw_name)

            if admin_uname:
                invite_url = f"https://t.me/{admin_uname.lstrip('@')}"
                admin_link_html = f'<a href="{invite_url}"><b>{clean_admin_name}</b></a>'
                kb_invite = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"💬 {clean_admin_name[:20]} ile Sohbeti Başlat", url=invite_url)]
                ])
            else:
                admin_link_html = f'<a href="tg://user?id={user_id}"><b>{clean_admin_name}</b></a>'
                kb_invite = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✉️ Bot Üzerinden Yanıt Yaz", callback_data="open_feedback_user")]
                ])

            invite_card = (
                "👤 <b>NUN PROJECT // YÖNETİCİ GÖRÜŞME TALEBİ</b>\n\n"
                f"Bot yöneticimiz {admin_link_html} sizinle doğrudan kişisel olarak görüşmek istiyor.\n\n"
                "Yöneticimizle 1:1 Telegram sohbetini hemen başlatmak için aşağıdaki bağlantıyı kullanabilirsiniz:"
            )

            try:
                await context.bot.send_message(chat_id=target_uid, text=invite_card, parse_mode="HTML", reply_markup=kb_invite)
                admin_confirm_text = (
                    "⚡ <b>1:1 Özel Sohbet Daveti İletildi!</b>\n\n"
                    f"Alıcı: <b>{clean_target_name}</b> (<code>{target_uid}</code>)\n\n"
                    "Kullanıcıya özel mesaj davetiniz ve profil bağlantınız başarıyla ulaştırıldı."
                )
                await query.message.reply_text(admin_confirm_text, parse_mode="HTML")
                await safe_answer("✅ Davet başarıyla gönderildi!")
            except Exception as e:
                err_text = f"❌ <b>Davet iletilemedi:</b> <code>{html_lib.escape(str(e))}</code>\n<i>(Kullanıcı botu engellemiş olabilir)</i>"
                await query.message.reply_text(err_text, parse_mode="HTML")
                await safe_answer("❌ İletilemedi", show_alert=True)
        return

    if data == "open_feedback_user":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'feedback_input'
        await query.message.reply_text(
            get_text(user_id, 'prompt_feedback', context),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
        )
        return

    if data == "admin_abort_broadcast":
        if user_id in ADMIN_IDS:
            global BROADCAST_ABORT_FLAG
            BROADCAST_ABORT_FLAG = True
            await query.answer("🛑 Duyuru durduruluyor...", show_alert=True)
        return

    if data == "admin_sync_users_prompt":
        if user_id in ADMIN_IDS:
            await safe_answer("⏳ Kullanıcılar eşitleniyor...", show_alert=False)
            all_u = get_all_registered_users()
            for u in all_u:
                try:
                    chat = await context.bot.get_chat(chat_id=u['id'])
                    p = USER_PROFILES.get(str(u['id']), {})
                    p['id'] = u['id']
                    p['name'] = (f"{chat.first_name or ''} {chat.last_name or ''}").strip() or f"User {u['id']}"
                    p['username'] = (chat.username or "").lstrip('@')
                    USER_PROFILES[str(u['id'])] = p
                except Exception:
                    pass
            save_json(USER_PROFILES_FILE, USER_PROFILES)
            invalidate_users_cache()
            text, kb = format_users_directory_page(0)
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="HTML")
        return

    if data == "admin_search_prompt":
        if user_id in ADMIN_IDS:
            context.user_data['admin_search_mode'] = True
            await query.message.reply_text(
                "🔍 *KULLANICI ARAMA MOTORU*\n\nLütfen Telegram ID'sini, ismini veya @kullanıcıadını yazıp gönderin:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
            )
        return

    if data == "admin_restore_prompt":
        if user_id in ADMIN_IDS:
            context.user_data['admin_restore_mode'] = True
            await query.message.reply_text(
                "📥 *VERİTABANI GERİ YÜKLEME*\n\nLütfen `nun_bot_backup_*.zip` dosyasını bu sohbete belge olarak gönderin:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
            )
        return

    if data == "admin_restart_prompt":
        if user_id in ADMIN_IDS:
            kb_reboot = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Evet, Botu Yeniden Başlat", callback_data="admin_confirm_restart")],
                [InlineKeyboardButton("❌ İptal", callback_data="cancel_action")]
            ])
            await query.message.reply_text(
                "⚠️ *BOTU YENİDEN BAŞLATMA ONAYI*\n\nBot tüm açık oturumları güvenle tamamlayıp temiz bir süreç olarak baştan başlayacaktır. Onaylıyor musunuz?",
                parse_mode="Markdown",
                reply_markup=kb_reboot
            )
        return

    if data == "admin_confirm_restart":
        if user_id in ADMIN_IDS:
            await safe_answer()
            await query.message.edit_text("🔄 *Bot yeniden başlatılıyor, lütfen bekleyiniz...*", parse_mode="Markdown")
            await notify_admin_audit_log(context.bot, user_id, "Botu uzaktan yeniden başlattı (Restart).")
            def _reboot():
                time.sleep(1.0)
                try:
                    script_path = os.path.abspath(__file__)
                    os.execv(sys.executable, [sys.executable, script_path])
                except Exception:
                    sys.exit(0)
            threading.Thread(target=_reboot, daemon=True).start()
        return

    if data.startswith("admin_bc_target_"):
        if user_id in ADMIN_IDS:
            target_seg = data.replace("admin_bc_target_", "", 1)
            context.user_data['broadcast_target_segment'] = target_seg
            context.user_data['admin_broadcast_mode'] = True
            seg_title = {"all": "Tüm Kullanıcılar", "tr": "Türkçe (TR)", "uz": "Özbekçe (UZ)", "ru": "Rusça (RU)", "en": "İngilizce (EN)", "prayer": "Ezan Bildirimi Açıklar"}.get(target_seg, target_seg)
            await query.message.reply_text(
                f"📢 *TOPLU DUYURU: [{seg_title.upper()}]*\n\nLütfen göndermek istediğiniz duyuru metnini yazıp gönderin:\n\n_(İptal için /cancel)_",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
            )
        return

    if data == "admin_backup_download":
        if user_id in ADMIN_IDS:
            status_wait = await query.message.reply_text("⏳ Yedek hazırlanıyor...")
            await send_backup_to_admins(context.bot, trigger_type="Panel İndirme Talebi")
            try:
                await status_wait.delete()
            except Exception:
                pass
        return

    if data == "admin_view_logs":
        if user_id in ADMIN_IDS:
            await safe_answer()
            card, kb = format_system_logs_card()
            await safe_edit_text_markup(query.message, card, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "admin_db_health":
        if user_id in ADMIN_IDS:
            await safe_answer()
            ok, msg = check_and_repair_databases()
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Sistem Paneline Dön", callback_data="admin_hub_system")]])
            await safe_edit_text_markup(query.message, msg, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "admin_bc_preview":
        if user_id in ADMIN_IDS:
            b_text = context.user_data.get('pending_broadcast_text')
            if b_text:
                b_card = f"📢 *NUN PROJECT // RESMİ DUYURU (ÖNİZLEME)*\n\n{b_text}\n\n_Bu bir yönetici önizlemesidir._"
                await context.bot.send_message(chat_id=user_id, text=b_card, parse_mode="Markdown")
            return

    if data == "admin_export_excel":
        if user_id in ADMIN_IDS:
            await safe_answer("📊 Excel tablosu hazırlanıyor...", show_alert=False)
            status_ex = await query.message.reply_text("⏳ Excel tablosu oluşturuluyor...")
            with tempfile.TemporaryDirectory() as tmp_dir:
                excel_path = os.path.join(tmp_dir, "nun_bot_kullanicilar.xlsx")
                ok = export_users_to_excel(excel_path)
                if ok and os.path.exists(excel_path):
                    with open(excel_path, "rb") as f_ex:
                        await context.bot.send_document(
                            chat_id=user_id,
                            document=f_ex,
                            filename="nun_bot_kullanicilar.xlsx",
                            caption=f"📊 *KULLANICI LİSTESİ (EXCEL)*\n👥 Toplam: `{len(get_all_registered_users())}`",
                            parse_mode="Markdown"
                        )
            try:
                await status_ex.delete()
            except Exception:
                pass
        return

    if data == "open_qaza_tracker":
        await safe_answer()
        card_text = format_qaza_card(user_id, user_lang)
        kb = build_qaza_keyboard(user_id, user_lang)
        await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("qaza_add_"):
        prayer_name = data.replace("qaza_add_", "", 1)
        update_user_qaza(user_id, prayer_name, +1)
        await safe_answer("➕ 1 eklendi", show_alert=False)
        card_text = format_qaza_card(user_id, user_lang)
        kb = build_qaza_keyboard(user_id, user_lang)
        await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("qaza_sub_"):
        prayer_name = data.replace("qaza_sub_", "", 1)
        update_user_qaza(user_id, prayer_name, -1)
        await safe_answer("➖ 1 tamamlandı", show_alert=False)
        card_text = format_qaza_card(user_id, user_lang)
        kb = build_qaza_keyboard(user_id, user_lang)
        await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "qaza_reset_prompt":
        reset_user_qaza(user_id)
        await safe_answer("🔄 Kaza namazları sıfırlandı!", show_alert=True)
        card_text = format_qaza_card(user_id, user_lang)
        kb = build_qaza_keyboard(user_id, user_lang)
        await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "qaza_back_prayer":
        await safe_answer()
        saved_city = get_user_city(user_id) or "Shahar"
        u_coords = get_user_coords(user_id)
        if u_coords:
            timings, d_name, dt_s, h_s, src = await fetch_prayer_times_by_coord(
                u_coords['lat'], u_coords['lon'], u_coords.get('elevation', 0.0), saved_city, "", u_coords.get('country', ''), user_id=user_id
            )
            if timings:
                p_card = format_prayer_card(d_name, timings, dt_s, h_s, src, user_lang, user_now=get_user_now(user_id, context), user_id=user_id)
                p_kb = get_prayer_hub_keyboard(user_id, user_lang)
                await safe_edit_text_markup(query.message, p_card, reply_markup=p_kb, parse_mode="Markdown")
                return
        await query.message.reply_text(get_text(user_id, 'prompt_prayer', context), parse_mode="Markdown")
        return

    if data == "open_ramadan_countdown":
        await safe_answer()
        card_rc = format_ramadan_countdown_card(user_id, user_lang, user_now=get_user_now(user_id, context))
        kb_rc = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Namoz Markaziga Qaytish", callback_data="qaza_back_prayer")]])
        await safe_edit_text_markup(query.message, card_rc, reply_markup=kb_rc, parse_mode="Markdown")
        return

    if data == "dl_audio_extract":
        await safe_answer("🎵 MP3 hazırlanıyor...")
        target_url = context.user_data.get('last_video_url')
        if target_url:
            status_aud = await query.message.reply_text(get_text(user_id, 'audio_downloading', context))
            try:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    aud_res = await asyncio.to_thread(download_audio_sync, target_url, tmp_dir)
                    aud_path = aud_res['path']
                    with open(aud_path, 'rb') as f_aud:
                        await context.bot.send_audio(
                            chat_id=user_id,
                            audio=f_aud,
                            title=aud_res.get('title', 'Audio')[:60],
                            caption=f"🎵 {aud_res.get('title', 'Audio')[:60]}"
                        )
            except Exception as e:
                add_system_log(f"Audio indirme hatasi: {e}", level="WARN")
                await query.message.reply_text(get_text(user_id, 'video_error', context))
            finally:
                try:
                    await status_aud.delete()
                except Exception:
                    pass
        return

    if data == "admin_clean_disk":
        if user_id in ADMIN_IDS:
            freed_mb = cleanup_orphaned_temp_files(max_age_seconds=0)
            disk_now = get_disk_usage_info()
            await safe_answer(f"🧹 {freed_mb:.1f} MB çöp temizlendi! | Disk: {disk_now}", show_alert=True)
            text, kb = format_admin_system_hub()
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "admin_toggle_maintenance":
        if user_id in ADMIN_IDS:
            new_st = toggle_maintenance_mode()
            st_text = "AÇILDI (Yalnızca yöneticiler)" if new_st else "KAPATILDI (Herkes erişebilir)"
            await safe_answer(f"🚧 Bakım Modu {st_text}", show_alert=True)
            text, kb = format_admin_security_hub()
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "admin_banned_list":
        if user_id in ADMIN_IDS:
            await safe_answer()
            if not BANNED_USERS:
                text = "⛔ *ENGELLENMİŞ KULLANICILAR*\n\n_Sistemde banlı kullanıcı bulunmuyor._"
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kullanıcı Merkezine Dön", callback_data="admin_hub_users")]])
                await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="Markdown")
                return
            b_lines = [f"⛔ *ENGELLENMİŞ KULLANICILAR LİSTESİ:* ({len(BANNED_USERS)} kişi)\n"]
            b_btns = []
            for b_uid, b_info in list(BANNED_USERS.items()):
                p = USER_PROFILES.get(b_uid, {})
                nm = safe_md(p.get('name') or f"User {b_uid}")
                b_lines.append(f"▫️ {nm} (`{b_uid}`) — _{safe_md(b_info.get('reason'))}_\n")
                b_btns.append([
                    InlineKeyboardButton(f"👤 {nm[:15]}", callback_data=f"admin_user_view_{b_uid}"),
                    InlineKeyboardButton("✅ Engeli Kaldır", callback_data=f"admin_unban_{b_uid}")
                ])
            b_btns.append([InlineKeyboardButton("🔙 Kullanıcı Merkezine Dön", callback_data="admin_hub_users")])
            await safe_edit_text_markup(query.message, "\n".join(b_lines), reply_markup=InlineKeyboardMarkup(b_btns), parse_mode="Markdown")
        return

    if data.startswith("admin_user_view_"):
        if user_id in ADMIN_IDS:
            await safe_answer()
            target_uid = int(data.replace("admin_user_view_", "", 1))
            card_text, kb = format_user_detail_card(target_uid)
            await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("admin_fetch_chat_"):
        if user_id in ADMIN_IDS:
            target_uid = int(data.replace("admin_fetch_chat_", "", 1))
            try:
                chat = await context.bot.get_chat(chat_id=target_uid)
                uid_str = str(target_uid)
                p = USER_PROFILES.get(uid_str, {})
                p['id'] = target_uid
                p['name'] = (f"{chat.first_name or ''} {chat.last_name or ''}").strip() or f"User {target_uid}"
                p['username'] = (chat.username or "").lstrip('@')
                USER_PROFILES[uid_str] = p
                save_json(USER_PROFILES_FILE, USER_PROFILES)
                invalidate_users_cache()
                await safe_answer("✅ Bilgiler güncellendi!", show_alert=True)
            except Exception as e:
                await safe_answer(f"⚠️ Bilgi çekilemedi: {e}", show_alert=True)
            card_text, kb = format_user_detail_card(target_uid)
            await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("admin_ban_choose_"):
        if user_id in ADMIN_IDS:
            await safe_answer()
            target_uid = int(data.replace("admin_ban_choose_", "", 1))
            text, kb = format_user_ban_options_card(target_uid)
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("admin_ban_soft_prompt_"):
        if user_id in ADMIN_IDS:
            target_uid = int(data.replace("admin_ban_soft_prompt_", "", 1))
            context.user_data['admin_ban_target'] = target_uid
            context.user_data['admin_ban_mode'] = 'soft'
            await query.message.reply_text("🟡 Standart ban sebebi yazınız:", parse_mode="Markdown")
        return

    if data.startswith("admin_ban_hard_prompt_"):
        if user_id in ADMIN_IDS:
            target_uid = int(data.replace("admin_ban_hard_prompt_", "", 1))
            context.user_data['admin_ban_target'] = target_uid
            context.user_data['admin_ban_mode'] = 'hard'
            await query.message.reply_text("🔴 Tam men sebebi yazınız:", parse_mode="Markdown")
        return

    if data.startswith("admin_ban_soft_quick_"):
        if user_id in ADMIN_IDS:
            target_uid = int(data.replace("admin_ban_soft_quick_", "", 1))
            await execute_soft_ban(context.bot, target_uid, reason="Kural ihlali")
            await safe_answer("🟡 Standart ban uygulandı!", show_alert=True)
            card_text, kb = format_user_detail_card(target_uid)
            await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("admin_ban_hard_quick_"):
        if user_id in ADMIN_IDS:
            target_uid = int(data.replace("admin_ban_hard_quick_", "", 1))
            await execute_hard_ban(context.bot, target_uid, reason="Kural ihlali")
            await safe_answer("🔴 Kullanıcı bottan atıldı!", show_alert=True)
            card_text, kb = format_user_detail_card(target_uid)
            await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("admin_unban_"):
        if user_id in ADMIN_IDS:
            target_uid = int(data.replace("admin_unban_", "", 1))
            unban_user(target_uid)
            await safe_answer("✅ Engel kaldırıldı!", show_alert=True)
            card_text, kb = format_user_detail_card(target_uid)
            await safe_edit_text_markup(query.message, card_text, reply_markup=kb, parse_mode="HTML")
        return

    if data == "stats_refresh":
        if user_id in ADMIN_IDS:
            report, kb = format_admin_stats_panel()
            await safe_edit_text_markup(query.message, report, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "stats_back_main":
        if user_id in ADMIN_IDS:
            report, kb = format_admin_stats_panel()
            await safe_edit_text_markup(query.message, report, reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("stats_users_page_"):
        if user_id in ADMIN_IDS:
            p_num = int(data.replace("stats_users_page_", "", 1))
            text, kb = format_users_directory_page(p_num)
            await safe_edit_text_markup(query.message, text, reply_markup=kb, parse_mode="HTML")
        return

    if data == "admin_panel_close":
        if user_id in ADMIN_IDS:
            try:
                await query.message.delete()
            except Exception:
                pass
        return

    if data == "confirm_broadcast_send":
        if user_id in ADMIN_IDS:
            b_text = context.user_data.pop('pending_broadcast_text', None)
            if b_text:
                target_seg = context.user_data.pop('broadcast_target_segment', 'all')
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await run_segmented_broadcast(context.bot, user_id, b_text, target_segment=target_seg)
        return

    if data == "admin_broadcast_prompt":
        if user_id in ADMIN_IDS:
            msg, kb = format_broadcast_audience_menu()
            await safe_edit_text_markup(query.message, msg, reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("admin_dm_start_"):
        if user_id in ADMIN_IDS:
            target_uid = int(data.replace("admin_dm_start_", "", 1))
            context.user_data['admin_dm_target'] = target_uid
            p = USER_PROFILES.get(str(target_uid), {})
            nm = safe_md(p.get('name') or f"User {target_uid}")
            await query.message.reply_text(f"✍️ *{nm}* (`{target_uid}`) kullanıcısına mesajınızı yazınız:", parse_mode="Markdown")
        return

    if data == "cancel_action":
        cleanup_user_temp_files(context, user_id)
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text=get_text(user_id, 'cancel_success', context), reply_markup=get_reply_menu(user_id, context))
        return

    if data.startswith("lang_"):
        l_code = data.replace("lang_", "", 1).strip()
        save_user_lang(user_id, l_code)
        silent_background_tz_sync(user_id, user_lang_code=l_code)
        if context and context.user_data is not None:
            context.user_data['lang'] = l_code
            context.user_data['mode'] = 'awaiting_city_after_lang'
        try:
            await query.message.delete()
        except Exception:
            pass
        await update_user_bot_commands(context, user_id, l_code)
        t_new = TEXTS[l_code]
        prompt_msg = f"✅ *{t_new['lang_changed']}*\n\n📍 *{t_new['prompt_city_sync_title']}*\n{t_new['prompt_city_sync_desc']}"
        await context.bot.send_message(chat_id=user_id, text=prompt_msg, parse_mode="Markdown", reply_markup=get_reply_menu(user_id, context))
        return

    if data == "open_tz_selector":
        await query.message.reply_text(get_text(user_id, 'prompt_timezone', context), parse_mode="Markdown", reply_markup=get_timezone_keyboard(user_lang))
        return

    if data == "tz_req_location":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'awaiting_location_or_city'
        await query.message.reply_text(get_text(user_id, 'prompt_send_location', context), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]]))
        return

    if data.startswith("settz_"):
        city_tz_defaults = {
            "settz_5": ("Toshkent", 5), "settz_3": ("Istanbul", 3), "settz_3_ru": ("Moskva", 3),
            "settz_4": ("Dubay", 4), "settz_0": ("London", 0), "settz_1": ("Berlin", 1),
            "settz_5_kz": ("Olmaota", 5), "settz_-5": ("New York", -5),
        }
        city_name, val = city_tz_defaults.get(data, (None, None))
        if val is None:
            try:
                val = int(data.split("_")[1])
            except Exception:
                val = 3
        if city_name:
            save_user_city(user_id, city_name)
        save_user_timezone(user_id, val, locked=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        user_now = get_user_now(user_id, context)
        now_str = user_now.strftime("%H:%M")
        tz_sign = "+" if val >= 0 else ""
        c_label = f"\n📍 {t['btn_city_label']}: *{city_name}*" if city_name else ""
        msg = f"✅ *{t['tz_changed']}*{c_label}\n\n🕒 {t['btn_timezone']}: `UTC{tz_sign}{val}`\n⏰ {t['current_time_lbl']}: `{now_str}`\n\n_{t['tz_synced_hint']}_"
        await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        return

    log_module_action('hadith', user_id)
    if data.startswith("open_hadith_"):
        offset = int(data.replace("open_hadith_", "", 1))
        now = get_user_now(user_id, context)
        h_obj, cur_idx = get_daily_hadith(now, offset)
        h_card = format_daily_hadith_card(h_obj, now, user_lang)
        lbl_next = {'uz': "🔄 Boshqa hadis", 'tr': "🔄 Başka Hadis", 'ru': "🔄 Другой хадис", 'en': "🔄 Another Hadith"}.get(user_lang, "🔄 Next")
        kb_h = InlineKeyboardMarkup([
            [InlineKeyboardButton(lbl_next, callback_data=f"open_hadith_{offset+1}")],
            [InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]
        ])
        await query.message.reply_text(h_card, parse_mode="Markdown", reply_markup=kb_h)
        return

    if data == "open_hijri_cal":
        now = get_user_now(user_id, context)
        card_cal = format_islamic_calendar_card(now, user_lang)
        await query.message.reply_text(card_cal, parse_mode="Markdown")
        return

    if data == "show_kerahat_info":
        guide_text = get_kerahat_detailed_guide(user_lang)
        await query.message.reply_text(guide_text, parse_mode="Markdown")
        return

    if data == "gen_imsakiye_pdf":
        u_coords = get_user_coords(user_id)
        saved_c = get_user_city(user_id) or "Shahar"
        if not u_coords:
            await query.message.reply_text("📍 " + get_text(user_id, 'prompt_prayer', context))
            return
        status = await query.message.reply_text("⏳ " + get_text(user_id, 'doc_processing', context))
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_pdf = os.path.join(tmp_dir, f"imsakiye_{user_id}.pdf")
                tz_off = get_user_tz_offset(user_id, context)
                generate_imsakiye_pdf(out_pdf, saved_c, u_coords['lat'], u_coords['lon'], u_coords.get('elevation', 0.0), float(tz_off), u_coords.get('country', ''), user_lang)
                cap = f"📄 *{saved_c}* — 30 kunlik namoz taqvimi (PDF)"
                with open(out_pdf, "rb") as f_doc:
                    await query.message.reply_document(document=f_doc, filename=f"imsakiye_{user_lang}.pdf", caption=cap, parse_mode="Markdown")
        finally:
            gc.collect()
            try:
                await status.delete()
            except Exception:
                pass
        return

    if data == "open_todo_hub":
        now = get_user_now(user_id, context)
        card_t = format_todo_card(user_id, now, user_lang)
        kb_t = build_todo_keyboard(user_id, user_lang)
        await query.message.reply_text(card_t, parse_mode="Markdown", reply_markup=kb_t)
        return

    if data.startswith("todo_tog_"):
        idx_t = int(data.replace("todo_tog_", "", 1))
        toggle_user_todo(user_id, idx_t)
        now = get_user_now(user_id, context)
        card_t = format_todo_card(user_id, now, user_lang)
        kb_t = build_todo_keyboard(user_id, user_lang)
        await safe_edit_text_markup(query.message, card_t, reply_markup=kb_t, parse_mode="Markdown")
        return

    if data == "todo_clear":
        clear_completed_todos(user_id)
        now = get_user_now(user_id, context)
        card_t = format_todo_card(user_id, now, user_lang)
        kb_t = build_todo_keyboard(user_id, user_lang)
        await safe_edit_text_markup(query.message, card_t, reply_markup=kb_t, parse_mode="Markdown")
        return

    if data == "todo_add":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'todo_input'
        await query.message.reply_text(get_text(user_id, 'prompt_todo_add', context), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]]))
        return

    if data == "exam_back_hub":
        exams = get_user_exams(user_id)
        now = get_user_now(user_id, context)
        cards = []
        if exams:
            for e in exams:
                cd_str = format_exam_countdown(e.get('date', ''), now, user_lang)
                cards.append(f"📌 *{e.get('title')}*\n📅 `{e.get('date')}`\n{cd_str}\n")
        else:
            cards = [get_text(user_id, 'exam_empty', context)]
        hdr = get_text(user_id, 'exam_hub_title', context)
        kb_ex = InlineKeyboardMarkup([
            [InlineKeyboardButton(get_text(user_id, 'exam_btn_add', context), callback_data="exam_add"),
             InlineKeyboardButton(get_text(user_id, 'btn_exam_todo', context), callback_data="open_todo_hub")]
        ])
        await safe_edit_text_markup(query.message, f"{hdr}\n\n" + "\n".join(cards), reply_markup=kb_ex, parse_mode="Markdown")
        return

    if data == "open_adhkar_hub":
        await query.message.reply_text(get_text(user_id, 'prompt_adhkar', context), reply_markup=get_adhkar_selection_keyboard(user_lang))
        return

    if data == "open_prayer_notif_menu":
        cfg = get_user_prayer_notif(user_id)
        cur_status = "✅ " + t['notif_btn_on_time'] if (cfg.get("enabled") and cfg.get("offset")==0) else ("⏱️ " + t['notif_btn_15m'] if (cfg.get("enabled") and cfg.get("offset")==15) else "🔕 " + t['notif_btn_off'])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t['notif_btn_on_time'], callback_data="set_pnotif_0")],
            [InlineKeyboardButton(t['notif_btn_15m'], callback_data="set_pnotif_15")],
            [InlineKeyboardButton(t['notif_btn_off'], callback_data="set_pnotif_off")],
            [InlineKeyboardButton(t['btn_cancel'], callback_data="cancel_action")],
        ])
        msg = f"{t['notif_menu_title']}\n\n{t['notif_menu_desc']}\n\n📌 *{cur_status}*"
        await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("set_pnotif_"):
        mode_val = data.replace("set_pnotif_", "", 1)
        if mode_val == "off":
            toggle_user_prayer_notif(user_id, "disable")
        elif mode_val == "15":
            toggle_user_prayer_notif(user_id, "15min")
        else:
            toggle_user_prayer_notif(user_id, "on_time")
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text=f"✅ {t['notif_saved']}")
        return

    if data.startswith("sel_prayer_cand_"):
        idx = int(data.replace("sel_prayer_cand_", "", 1))
        cands = context.user_data.get('prayer_candidates', [])
        if 0 <= idx < len(cands):
            c = cands[idx]
            context.user_data.pop('prayer_candidates', None)
            cleanup_user_temp_files(context, user_id)
            timings, d_name, dt_s, h_s, src = await fetch_prayer_times_by_coord(
                c['lat'], c['lon'], c['elevation'], c['name'], c['admin1'], c['country'], c['timezone'], user_id=user_id
            )
            if timings:
                card = format_prayer_card(d_name, timings, dt_s, h_s, src, user_lang, user_now=get_user_now(user_id, context), user_id=user_id)
                kb = get_prayer_hub_keyboard(user_id, user_lang)
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(chat_id=user_id, text=card, parse_mode="Markdown", reply_markup=kb)
                return

    if data == "change_prayer_city":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'prayer'
        recents = get_recent_cities(user_id)
        kb_rows = []
        if recents:
            recent_btns = [InlineKeyboardButton(f"📍 {rc}", callback_data=f"sel_recent_city_{rc}") for rc in recents[:2]]
            kb_rows.append(recent_btns)
        kb_rows.append([InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")])
        await query.message.reply_text(
            get_text(user_id, 'prompt_prayer', context),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb_rows)
        )
        return

    if data.startswith("sel_recent_city_"):
        c_name = data.replace("sel_recent_city_", "", 1)
        cleanup_user_temp_files(context, user_id)
        res_p = await fetch_prayer_times(c_name, user_id=user_id, user_lang=user_lang)
        timings, d_name, dt_s, h_s, src = res_p[0], res_p[1], res_p[2], res_p[3], res_p[4]
        if timings:
            card = format_prayer_card(d_name, timings, dt_s, h_s, src, user_lang, user_now=get_user_now(user_id, context), user_id=user_id)
            kb = get_prayer_hub_keyboard(user_id, user_lang)
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user_id, text=card, parse_mode="Markdown", reply_markup=kb)
            return

    if data == "change_weather_city":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'weather'
        await query.message.reply_text(
            get_text(user_id, 'prompt_weather', context),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
        )
        return

    if data == "adhkar_morning":
        await query.message.reply_text(TEXTS[user_lang]['adhkar_morning_text'], parse_mode="Markdown")
        return

    if data == "adhkar_evening":
        await query.message.reply_text(TEXTS[user_lang]['adhkar_evening_text'], parse_mode="Markdown")
        return

    if data == "adhkar_salawat":
        await query.message.reply_text(TEXTS[user_lang]['adhkar_salawat_text'], parse_mode="Markdown")
        return

    if data == "pdf_act_to_pdf":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'convert_to_pdf'
        await query.message.reply_text(
            get_text(user_id, 'prompt_convert_to_pdf', context),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
        )
        return

    if data == "pdf_act_ocr":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'ocr'
        await query.message.reply_text(
            get_text(user_id, 'prompt_ocr', context),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
        )
        return

    if data == "direct_img_pdf":
        img_p = context.user_data.get('direct_file_path')
        if img_p and os.path.exists(img_p):
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_p = os.path.join(tmp_dir, "converted.pdf")
                with Image.open(img_p) as im:
                    if im.mode in ("RGBA", "P"):
                        im = im.convert("RGB")
                    im.save(out_p, format="PDF")
                with open(out_p, "rb") as f:
                    await query.message.reply_document(document=f, filename="converted.pdf", caption=get_text(user_id, 'pdf_ready', context))
        cleanup_user_temp_files(context, user_id)
        return

    if data == "direct_img_ocr":
        img_p = context.user_data.get('direct_file_path')
        if img_p and os.path.exists(img_p):
            txt = await asyncio.to_thread(extract_text_from_image, img_p)
            if txt:
                if len(txt) > 3500:
                    with tempfile.TemporaryDirectory() as t_dir:
                        t_file = os.path.join(t_dir, "ocr_text.txt")
                        with open(t_file, "w", encoding="utf-8") as f_out:
                            f_out.write(txt)
                        with open(t_file, "rb") as f_send:
                            await query.message.reply_document(document=f_send, filename="ocr_text.txt", caption=get_text(user_id, 'ocr_title', context))
                else:
                    await query.message.reply_text(f"{get_text(user_id, 'ocr_title', context)}\n\n`{txt}`", parse_mode="Markdown")
            else:
                await query.message.reply_text(get_text(user_id, 'ocr_fail', context))
        cleanup_user_temp_files(context, user_id)
        return

    if data.startswith("pomo_"):
        mins = int(data.replace("pomo_", "", 1))
        is_break = mins in (5, 10)
        user_now = get_user_now(user_id, context)
        end_time = user_now + timedelta(minutes=mins)
        tz_off = get_user_tz_offset(user_id, context)
        tz_str = f"UTC+{tz_off}" if tz_off >= 0 else f"UTC{tz_off}"

        label = get_text(user_id, 'pomo_break_label', context) if is_break else get_text(user_id, 'pomo_work_label', context)
        unit = get_text(user_id, 'pomo_mins_unit', context)
        hdr = get_text(user_id, 'pomo_started', context)
        lbl_mode = get_text(user_id, 'pomo_mode_lbl', context)
        lbl_dur = get_text(user_id, 'pomo_dur_lbl', context)
        lbl_end = get_text(user_id, 'pomo_end_lbl', context)

        card = (
            f"🍅 *{hdr}*\n\n"
            f"📌 *{lbl_mode}:* {label}\n"
            f"⏳ *{lbl_dur}:* `{mins} {unit}`\n"
            f"🏁 *{lbl_end}:* `{end_time.strftime('%H:%M')}` _({tz_str})_"
        )
        await query.message.reply_text(card, parse_mode="Markdown")
        asyncio.create_task(pomodoro_timer_task(context.bot, user_id, mins, is_break, user_lang))
        return

    if data == "remind_add":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'remind_input'
        await query.message.reply_text(get_text(user_id, 'prompt_remind', context), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]]))
        return

    if data == "remind_list":
        rems = get_user_reminders(user_id)
        if not rems:
            await query.message.reply_text(get_text(user_id, 'remind_empty', context))
            return
        lines = [f"📋 *{get_text(user_id, 'pomo_btn_my_reminds', context)}:*\n"]
        btns = []
        for r in rems:
            lines.append(f"▫️ {r.get('text')} — `{r.get('time')}`")
            btns.append([InlineKeyboardButton(f"❌ {r.get('text')[:15]}", callback_data=f"del_rem_{r.get('id')}")])
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
        return

    if data.startswith("del_rem_"):
        r_id = data.replace("del_rem_", "", 1)
        delete_user_reminder(user_id, r_id)
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(f"🗑️ {get_text(user_id, 'remind_deleted', context)}")
        return

    if data == "exam_add":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'exam_title_input'
        await query.message.reply_text(
            get_text(user_id, 'prompt_exam_title', context),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
        )
        return

    if data.startswith("sched_"):
        def_t = get_text(user_id, 'exam_default_title', context)
        cur_dt = context.user_data.get('exam_draft_dt') or ((get_user_now(user_id, context) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0))
        title = context.user_data.get('exam_draft_title', def_t)
        
        if data == "sched_day_prev":
            cur_dt -= timedelta(days=1)
        elif data == "sched_day_next":
            cur_dt += timedelta(days=1)
        elif data == "sched_hour_minus":
            cur_dt -= timedelta(hours=1)
        elif data == "sched_hour_plus":
            cur_dt += timedelta(hours=1)
        elif data == "sched_min_minus":
            cur_dt -= timedelta(minutes=15)
        elif data == "sched_min_plus":
            cur_dt += timedelta(minutes=15)
        elif data == "sched_jump_today": 
            now = get_user_now(user_id, context)
            cur_dt = cur_dt.replace(year=now.year, month=now.month, day=now.day)
        elif data == "sched_jump_tmrw": 
            tmrw = get_user_now(user_id, context) + timedelta(days=1)
            cur_dt = cur_dt.replace(year=tmrw.year, month=tmrw.month, day=tmrw.day)
        elif data == "sched_jump_week": 
            nxt = get_user_now(user_id, context) + timedelta(days=7)
            cur_dt = cur_dt.replace(year=nxt.year, month=nxt.month, day=nxt.day)
        elif data == "sched_open_cal":
            await safe_edit_text_markup(query.message, f"🗓 *{title}*", build_month_calendar(cur_dt.year, cur_dt.month, user_lang), parse_mode="Markdown")
            return
        elif data == "sched_confirm":
            full_dt = cur_dt.strftime("%Y-%m-%d %H:%M")
            exam_id = add_user_exam(user_id, title, full_dt)
            cleanup_user_temp_files(context, user_id)
            try:
                await query.message.delete()
            except Exception:
                pass
            card = f"📌 *{title}*\n📅 `{full_dt}`"
            await context.bot.send_message(chat_id=user_id, text=f"{get_text(user_id, 'exam_saved', context)}\n\n{card}", parse_mode="Markdown")
            return

        context.user_data['exam_draft_dt'] = cur_dt
        await safe_edit_text_markup(query.message, format_scheduler_card(title, cur_dt, user_lang), build_scheduler_keyboard(user_lang), parse_mode="Markdown")
        return

    if data.startswith("cal_pick_"):
        def_t = get_text(user_id, 'exam_default_title', context)
        d_str = data.replace("cal_pick_", "", 1).strip()
        y, m, d = [int(x) for x in d_str.split("-")]
        cur_dt = context.user_data.get('exam_draft_dt') or get_user_now(user_id, context).replace(hour=10, minute=0)
        new_dt = cur_dt.replace(year=y, month=m, day=d)
        context.user_data['exam_draft_dt'] = new_dt
        title = context.user_data.get('exam_draft_title', def_t)
        await safe_edit_text_markup(query.message, format_scheduler_card(title, new_dt, user_lang), build_scheduler_keyboard(user_lang), parse_mode="Markdown")
        return

    if data == "cal_back_panel":
        def_t = get_text(user_id, 'exam_default_title', context)
        cur_dt = context.user_data.get('exam_draft_dt') or get_user_now(user_id, context)
        title = context.user_data.get('exam_draft_title', def_t)
        await safe_edit_text_markup(query.message, format_scheduler_card(title, cur_dt, user_lang), build_scheduler_keyboard(user_lang), parse_mode="Markdown")
        return

    if data.startswith("cal_nav_"):
        def_t = get_text(user_id, 'exam_default_title', context)
        parts = data.split("_")
        _, _, ny, nm = parts
        title = context.user_data.get('exam_draft_title', def_t)
        await safe_edit_text_markup(query.message, f"🗓 *{title}*", build_month_calendar(int(ny), int(nm), user_lang), parse_mode="Markdown")
        return

# =====================================================================
# BELGE & DOSYA İŞLEYİCİSİ
# =====================================================================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    track_user_activity(user)
    if is_rate_limited(user_id, 0.5):
        return

    mode = context.user_data.get('mode', 'auto')
    doc = update.message.document
    if not doc:
        return

    fname = doc.file_name or "file"
    ext = os.path.splitext(fname).lower()
    status = await update.message.reply_text(get_text(user_id, 'doc_processing', context))

    try:
        file_obj = await doc.get_file()
        perm_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        perm_tmp.close()
        await file_obj.download_to_drive(perm_tmp.name)

        if user_id in ADMIN_IDS and ext == ".zip":
            ok, res_msg = restore_database_from_zip(perm_tmp.name)
            try:
                os.remove(perm_tmp.name)
            except Exception:
                pass
            if ok:
                await update.message.reply_text(f"✅ *Veritabanı Başarıyla Geri Yüklendi!*\n\n{res_msg}", parse_mode="Markdown")
                await notify_admin_audit_log(context.bot, user_id, f"Veritabanını ZIP yedek dosyasından geri yükledi ({res_msg}).")
            else:
                await update.message.reply_text(f"❌ *Geri Yükleme Başarısız:* {res_msg}", parse_mode="Markdown")
            cleanup_user_temp_files(context, user_id)
            return

        if mode == 'ocr' and ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            txt = await asyncio.to_thread(extract_text_from_image, perm_tmp.name)
            if txt:
                if len(txt) > 3500:
                    with tempfile.TemporaryDirectory() as t_dir:
                        t_file = os.path.join(t_dir, "ocr_text.txt")
                        with open(t_file, "w", encoding="utf-8") as f_out:
                            f_out.write(txt)
                        with open(t_file, "rb") as f_send:
                            await update.message.reply_document(document=f_send, filename="ocr_text.txt", caption=get_text(user_id, 'ocr_title', context))
                else:
                    await update.message.reply_text(f"{get_text(user_id, 'ocr_title', context)}\n\n`{txt}`", parse_mode="Markdown")
            else:
                await update.message.reply_text(get_text(user_id, 'ocr_fail', context))
            cleanup_user_temp_files(context, user_id)
            try:
                os.remove(perm_tmp.name)
            except Exception:
                pass
            return

        if mode == 'convert_to_pdf':
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_pdf = os.path.join(tmp_dir, "converted.pdf")
                ok = False
                if ext in (".docx", ".doc"):
                    ok = await asyncio.to_thread(docx_to_pdf, perm_tmp.name, out_pdf)
                elif ext in (".xlsx", ".xls"):
                    ok = await asyncio.to_thread(xlsx_to_pdf, perm_tmp.name, out_pdf)
                elif ext == ".txt":
                    ok = await asyncio.to_thread(txt_to_pdf, perm_tmp.name, out_pdf)
                elif ext in (".jpg", ".jpeg", ".png"):
                    with Image.open(perm_tmp.name) as im:
                        if im.mode in ("RGBA", "P"):
                            im = im.convert("RGB")
                        im.save(out_pdf, format="PDF")
                    ok = True
                if ok and os.path.exists(out_pdf):
                    with open(out_pdf, "rb") as f:
                        await update.message.reply_document(document=f, filename="converted.pdf", caption=get_text(user_id, 'pdf_ready', context))
                else:
                    await update.message.reply_text(get_text(user_id, 'pdf_fail', context))
            cleanup_user_temp_files(context, user_id)
            try:
                os.remove(perm_tmp.name)
            except Exception:
                pass
            return

        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            context.user_data['direct_file_path'] = perm_tmp.name
            await update.message.reply_text(
                f"{get_text(user_id, 'direct_img_prompt', context)} (`{fname}`)",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(get_text(user_id, 'btn_direct_pdf', context), callback_data="direct_img_pdf")],
                    [InlineKeyboardButton(get_text(user_id, 'btn_direct_ocr', context), callback_data="direct_img_ocr")],
                    [InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]
                ])
            )
            return

        if ext in (".docx", ".xlsx", ".txt"):
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_pdf = os.path.join(tmp_dir, "converted.pdf")
                ok = False
                if ext == ".docx":
                    ok = await asyncio.to_thread(docx_to_pdf, perm_tmp.name, out_pdf)
                elif ext == ".xlsx":
                    ok = await asyncio.to_thread(xlsx_to_pdf, perm_tmp.name, out_pdf)
                elif ext == ".txt":
                    ok = await asyncio.to_thread(txt_to_pdf, perm_tmp.name, out_pdf)
                if ok and os.path.exists(out_pdf):
                    with open(out_pdf, "rb") as f:
                        await update.message.reply_document(document=f, filename="converted.pdf", caption=get_text(user_id, 'pdf_ready', context))
            try:
                os.remove(perm_tmp.name)
            except Exception:
                pass
            return

        try:
            os.remove(perm_tmp.name)
        except Exception:
            pass
        await update.message.reply_text("⚠️")

    except Exception as e:
        print(f"Hata: {e}")
        await update.message.reply_text(get_text(user_id, 'error_general', context))
    finally:
        gc.collect()
        try:
            await status.delete()
        except Exception:
            pass

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    track_user_activity(user)
    if is_rate_limited(user_id, 0.5):
        return

    mode = context.user_data.get('mode', 'auto')
    photo = update.message.photo[-1]
    status = await update.message.reply_text(get_text(user_id, 'doc_processing', context))

    try:
        file_obj = await photo.get_file()
        perm_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        perm_tmp.close()
        await file_obj.download_to_drive(perm_tmp.name)

        if mode == 'ocr':
            txt = await asyncio.to_thread(extract_text_from_image, perm_tmp.name)
            if txt:
                if len(txt) > 3500:
                    with tempfile.TemporaryDirectory() as t_dir:
                        t_file = os.path.join(t_dir, "ocr_text.txt")
                        with open(t_file, "w", encoding="utf-8") as f_out:
                            f_out.write(txt)
                        with open(t_file, "rb") as f_send:
                            await update.message.reply_document(document=f_send, filename="ocr_text.txt", caption=get_text(user_id, 'ocr_title', context))
                else:
                    await update.message.reply_text(f"{get_text(user_id, 'ocr_title', context)}\n\n`{txt}`", parse_mode="Markdown")
            else:
                await update.message.reply_text(get_text(user_id, 'ocr_fail', context))
            cleanup_user_temp_files(context, user_id)
            try:
                os.remove(perm_tmp.name)
            except Exception:
                pass
            return

        if mode == 'convert_to_pdf':
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_pdf = os.path.join(tmp_dir, "converted.pdf")
                with Image.open(perm_tmp.name) as im:
                    if im.mode in ("RGBA", "P"):
                        im = im.convert("RGB")
                    im.save(out_pdf, format="PDF")
                with open(out_pdf, "rb") as f:
                    await update.message.reply_document(document=f, filename="converted.pdf", caption=get_text(user_id, 'pdf_ready', context))
            cleanup_user_temp_files(context, user_id)
            try:
                os.remove(perm_tmp.name)
            except Exception:
                pass
            return

        context.user_data['direct_file_path'] = perm_tmp.name
        await update.message.reply_text(
            get_text(user_id, 'direct_img_prompt', context),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(get_text(user_id, 'btn_direct_pdf', context), callback_data="direct_img_pdf")],
                [InlineKeyboardButton(get_text(user_id, 'btn_direct_ocr', context), callback_data="direct_img_ocr")],
                [InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]
            ])
        )

    except Exception as e:
        print(f"Hata: {e}")
        await update.message.reply_text(get_text(user_id, 'error_general', context))
    finally:
        gc.collect()
        try:
            await status.delete()
        except Exception:
            pass

# =====================================================================
# METİN MESAJ YÖNLENDİRİCİSİ (DÜZELTME 4: BÖLÜM ATLAMALARI KÖKTEN ÇÖZÜLDÜ)
# =====================================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    user_id = user.id
    track_user_activity(user)

    if is_user_banned(user_id):
        ban_info = get_user_ban_info(user_id) or {}
        if ban_info.get('mode') == 'hard':
            try:
                await update.message.reply_text("⛔ Bot erişiminiz sonlandırılmıştır.", reply_markup=ReplyKeyboardRemove())
            except Exception:
                pass
        return

    if is_maintenance_active() and user_id not in ADMIN_IDS:
        await update.message.reply_text(get_text(user_id, 'maintenance_msg', context), parse_mode="Markdown")
        return

    if is_rate_limited(user_id, 0.5):
        return

    if check_flood_shield(user_id):
        try:
            await update.message.reply_text(get_text(user_id, 'flood_jail_alert', context))
        except Exception:
            pass
        return

    chat_id = update.message.chat_id
    raw_text = update.message.text.strip()
    user_lang = get_user_lang(user_id, context)

    tele_code = user.language_code if user else None
    silent_background_tz_sync(user_id, raw_text=raw_text, user_lang_code=tele_code)

    # 1. EN BAŞTA: YENİLE & BAŞTAN BAŞLAT BUTONU / KOMUTU (DÜZELTME 3)
    reset_labels = [TEXTS[l].get('btn_reset', '') for l in TEXTS] + ["/reset", "/restart_user", "🔄 Yenile & Baştan Başlat", "🔄 Qayta boshlash & Tozalash"]
    if raw_text in reset_labels:
        curr_mid = update.message.message_id
        factory_reset_user(user_id)
        cleanup_user_temp_files(context, user_id)
        context.user_data.clear()
        context.user_data['mode'] = 'auto'
        await update_user_bot_commands(context, user_id, user_lang)
        await wipe_chat_history(context.bot, chat_id, curr_mid, count=80)
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_text(user_id, 'reset_success', context),
            parse_mode="Markdown",
            reply_markup=get_reply_menu(user_id, context)
        )
        return

    # 2. EN BAŞTA: TÜM MENÜ BUTONLARI KONTROLÜ (DÜZELTME 4: Asılı kalan modları derhal iptal eder!)
    btn_keys = {
        'btn_video': 'video', 'btn_prayer': 'prayer', 'btn_weather': 'weather',
        'btn_pdf_hub': 'pdf_hub', 'btn_exam': 'exam', 'btn_schedule_img': 'schedule_img',
        'btn_pomodoro': 'pomodoro', 'btn_translit': 'translit', 'btn_adhkar': 'adhkar',
        'btn_timezone_hub': 'tz_hub', 'btn_lang': 'lang', 'btn_feedback': 'feedback'
    }
    for b_key, mode_val in btn_keys.items():
        allowed_texts = [TEXTS[l].get(b_key, '') for l in TEXTS]
        if b_key == 'btn_translit':
            allowed_texts.extend(["🔤 Krill ⇄ Lotin", "🔤 Kiril ⇄ Latin", "🔤 Кирилл ⇄ Латиница", "🔤 Кириллица ⇄ Латиница"])
        elif b_key == 'btn_prayer':
            allowed_texts.extend(["🕌 Namoz vaqtlari", "🕌 Namaz Vakitleri", "🕌 Время намаза", "🕌 Prayer Times", "🕌 Namoz & Ibadat", "🕌 Namoz & Ibodat", "🕌 Namaz & İbadet"])
        elif b_key == 'btn_feedback':
            allowed_texts.extend(["💡 Fikr & Taklif", "💡 Fikr va taklif", "💡 Öneri & Destek", "💡 Öneri & Geri Bildirim", "💡 Отзыв и Поддержка", "💡 Feedback & Support"])
        elif b_key == 'btn_adhkar':
            allowed_texts.extend(["📿 Zikrlar & Salovatlar", "📿 Zikrlar & Salavatlar", "📿 Zikirler & Salavat", "📿 Зикры и Салаваты", "📿 Adhkar & Salawat"])

        if raw_text in allowed_texts:
            cleanup_user_temp_files(context, user_id)
            if mode_val == 'video':
                log_module_action('video', user_id)
                await update.message.reply_text(get_text(user_id, 'prompt_video', context))
            elif mode_val == 'prayer':
                log_module_action('prayer', user_id)
                saved_city = get_user_city(user_id)
                if saved_city:
                    status = await update.message.reply_text(get_text(user_id, 'prayer_loading', context))
                    try:
                        res_p = await fetch_prayer_times(saved_city, user_id=user_id, user_lang=user_lang)
                        timings, d_name, dt_s, h_s, src = res_p[0], res_p[1], res_p[2], res_p[3], res_p[4]
                    finally:
                        try:
                            await status.delete()
                        except Exception:
                            pass
                    if timings:
                        card = format_prayer_card(d_name, timings, dt_s, h_s, src, user_lang, user_now=get_user_now(user_id, context), user_id=user_id)
                        kb = get_prayer_hub_keyboard(user_id, user_lang)
                        await update.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)
                        return
                context.user_data['mode'] = 'prayer'
                await update.message.reply_text(get_text(user_id, 'prompt_prayer', context), parse_mode="Markdown")
            elif mode_val == 'weather':
                log_module_action('weather', user_id)
                saved_city = get_user_city(user_id)
                if saved_city:
                    status = await update.message.reply_text(get_text(user_id, 'weather_loading', context))
                    try:
                        w_res = await fetch_weather(saved_city, user_lang)
                    finally:
                        try:
                            await status.delete()
                        except Exception:
                            pass
                    if w_res[0]:
                        name, admin1, country, c_temp, f_like, hum, wind, code, t_min, t_max = w_res
                        card = format_weather_card(name, admin1, country, c_temp, f_like, hum, wind, code, t_min, t_max, user_lang)
                        kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton(get_text(user_id, 'btn_change_weather_city', context), callback_data="change_weather_city")]
                        ])
                        await update.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)
                        return
                context.user_data['mode'] = 'weather'
                await update.message.reply_text(get_text(user_id, 'prompt_weather', context), parse_mode="Markdown")
            elif mode_val == 'pdf_hub':
                log_module_action('pdf_hub', user_id)
                await update.message.reply_text(get_text(user_id, 'prompt_pdf_hub', context), parse_mode="Markdown", reply_markup=get_pdf_hub_keyboard(user_lang))
            elif mode_val == 'exam':
                log_module_action('exam_todo', user_id)
                exams = get_user_exams(user_id)
                now = get_user_now(user_id, context)
                if exams:
                    cards = []
                    for e in exams:
                        cd_str = format_exam_countdown(e.get('date', ''), now, user_lang)
                        cards.append(f"📌 *{e.get('title')}*\n📅 `{e.get('date')}`\n{cd_str}\n")
                else:
                    cards = [get_text(user_id, 'exam_empty', context)]
                hdr = get_text(user_id, 'exam_hub_title', context)
                await update.message.reply_text(
                    f"{hdr}\n\n" + "\n".join(cards),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(get_text(user_id, 'exam_btn_add', context), callback_data="exam_add"),
                         InlineKeyboardButton(get_text(user_id, 'btn_exam_todo', context), callback_data="open_todo_hub")]
                    ])
                )
            elif mode_val == 'schedule_img':
                context.user_data['mode'] = 'schedule_img_input'
                await update.message.reply_text(get_text(user_id, 'prompt_schedule_img', context), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]]))
            elif mode_val == 'pomodoro':
                log_module_action('pomodoro', user_id)
                await update.message.reply_text(get_text(user_id, 'prompt_pomodoro', context), parse_mode="Markdown", reply_markup=get_pomodoro_keyboard(user_id, user_lang))
            elif mode_val == 'adhkar':
                log_module_action('adhkar', user_id)
                await update.message.reply_text(get_text(user_id, 'prompt_adhkar', context), reply_markup=get_adhkar_selection_keyboard(user_lang))
            elif mode_val == 'feedback':
                context.user_data['mode'] = 'feedback_input'
                await update.message.reply_text(
                    get_text(user_id, 'prompt_feedback', context),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
                )
            elif mode_val == 'translit':
                log_module_action('translit', user_id)
                context.user_data['mode'] = 'translit'
                await update.message.reply_text(
                    get_text(user_id, 'prompt_translit', context),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]
                    ])
                )
            elif mode_val == 'tz_hub':
                tz_off = get_user_tz_offset(user_id, context)
                tz_sign = "+" if tz_off >= 0 else ""
                cur_time = get_user_now(user_id, context).strftime("%H:%M")
                saved_city = get_user_city(user_id) or ("Toshkent" if user_lang=='uz' else "Istanbul")
                t = TEXTS.get(user_lang, TEXTS['uz'])
                desc = (
                    f"📍 *{t['btn_city_label']}:* `{saved_city}`\n"
                    f"🕒 *{t['btn_timezone']}:* `UTC{tz_sign}{tz_off}`\n"
                    f"⏰ *{t['current_time_lbl']}:* `{cur_time}`\n\n"
                    f"_{t['tz_synced_hint']}_\n\n"
                    f"{t['tz_hub_instruction']}"
                )
                await update.message.reply_text(desc, parse_mode="Markdown", reply_markup=get_timezone_keyboard(user_lang))
            elif mode_val == 'lang':
                await update.message.reply_text("Tilni tanlang / Dil seçimi / Выберите язык / Select language:", reply_markup=get_language_keyboard())
            return

    # 3. YÖNETİCİ ÖZEL BUTONU
    admin_panel_labels = ["👑 Yönetici Paneli", "👑 Boshqaruv Paneli", "👑 Панель Управления", "👑 Admin Panel", "/admin", "/panel"]
    if raw_text in admin_panel_labels:
        if user_id in ADMIN_IDS:
            cleanup_user_temp_files(context, user_id)
            report, kb = format_admin_stats_panel()
            await update.message.reply_text(report, parse_mode="Markdown", reply_markup=kb, disable_web_page_preview=True)
            return

    # YÖNETİCİ ARAMA / BAN / DM İŞLEYİCİLERİ
    if user_id in ADMIN_IDS and context.user_data.get('admin_search_mode'):
        context.user_data.pop('admin_search_mode', None)
        matches = search_registered_users(raw_text)
        card_text, kb_search = format_search_results_card(matches, raw_text)
        await update.message.reply_text(card_text, parse_mode="HTML", reply_markup=kb_search, disable_web_page_preview=True)
        return

    if user_id in ADMIN_IDS and context.user_data.get('admin_ban_target'):
        target_uid = context.user_data.pop('admin_ban_target')
        ban_mode = context.user_data.pop('admin_ban_mode', 'soft')
        p = USER_PROFILES.get(str(target_uid), {})
        nm = safe_md(p.get('name') or f"User {target_uid}")
        if ban_mode == 'hard':
            await execute_hard_ban(context.bot, target_uid, reason=raw_text)
            await update.message.reply_text(f"🔴 *{nm}* (`{target_uid}`) *TAMAMEN MEN EDİLDİ*.", parse_mode="Markdown")
        else:
            await execute_soft_ban(context.bot, target_uid, reason=raw_text)
            await update.message.reply_text(f"🟡 *{nm}* (`{target_uid}`) *STANDART BANLANDI*.", parse_mode="Markdown")
        return

    if user_id in ADMIN_IDS and context.user_data.get('admin_dm_target'):
        target_uid = context.user_data.pop('admin_dm_target')
        await send_admin_dm_to_user(context.bot, user_id, target_uid, raw_text, update.message)
        return

    if user_id in ADMIN_IDS and context.user_data.get('admin_broadcast_mode'):
        context.user_data.pop('admin_broadcast_mode', None)
        context.user_data['pending_broadcast_text'] = raw_text
        all_u = get_all_registered_users()
        total_recipients = len(all_u)
        confirm_card = (
            f"📢 *TOPLU DUYURU ONAYI*\n\n"
            f"👥 *Toplam Alıcı:* `{total_recipients}` kayıtlı kullanıcı\n\n"
            f"📝 *İletilecek Mesaj:*\n{raw_text}\n\n"
            f"⚠️ Bu duyuruyu göndermek istediğinizden emin misiniz?"
        )
        kb_confirm = InlineKeyboardMarkup([
            [InlineKeyboardButton("👁️ Önizle (Bana Gönder)", callback_data="admin_bc_preview")],
            [InlineKeyboardButton("✅ Evet, Herkese Gönder", callback_data="confirm_broadcast_send")],
            [InlineKeyboardButton("❌ İptal Et", callback_data="cancel_action")]
        ])
        await update.message.reply_text(confirm_card, parse_mode="Markdown", reply_markup=kb_confirm)
        return

    mode = context.user_data.get('mode', 'auto') if context and context.user_data else 'auto'

    # KULLANICI GERİ BİLDİRİM GİRİŞİ
    if mode == 'feedback_input':
        cleanup_user_temp_files(context, user_id)
        u_name = safe_md((f"{user.first_name or ''} {user.last_name or ''}").strip() or f"User {user_id}")
        u_name_tag = f"(@{user.username})" if user.username else "_(Username yok)_"
        u_city = get_user_city(user_id) or "Belirtilmedi"
        ticket_msg = (
            f"📬 *YENİ DESTEK & ÖNERİ BİLDİRİMİ (TICKET)*\n\n"
            f"👤 Gönderen: *{u_name}* {u_name_tag}\n"
            f"🆔 ID: `{user_id}`\n"
            f"📍 Şehir: `{u_city}` | Dil: `{user_lang.upper()}`\n\n"
            f"💬 *Mesaj:*\n{safe_md(raw_text)}"
        )
        kb_ticket = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"⚡ 1:1 Sohbet Başlat", callback_data=f"admin_direct_invite_{user_id}"),
                InlineKeyboardButton(f"✉️ Bot İçi DM", callback_data=f"admin_dm_start_{user_id}")
            ]
        ])
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=aid, text=ticket_msg, parse_mode="Markdown", reply_markup=kb_ticket)
            except Exception:
                pass
        await update.message.reply_text(get_text(user_id, 'feedback_sent', context), parse_mode="Markdown")
        return

    # DİL SEÇİMİNDEN SONRA ŞEHİR GİRİŞİ
    if mode in ('awaiting_city_after_lang', 'awaiting_location_or_city'):
        tz_detected = parse_tz_from_text(raw_text)
        t = TEXTS.get(user_lang, TEXTS['uz'])
        if tz_detected is not None:
            save_user_timezone(user_id, tz_detected, locked=True)
            save_user_city(user_id, raw_text.title())
            cleanup_user_temp_files(context, user_id)
            user_now = get_user_now(user_id, context)
            now_str = user_now.strftime("%H:%M")
            tz_sign = "+" if tz_detected >= 0 else ""
            res_p = await fetch_prayer_times(raw_text, user_id=user_id, user_lang=user_lang)
            timings, d_name = res_p[0], (res_p[1] if isinstance(res_p[1], str) else raw_text.title())
            prayer_sync_text = f"\n\n🕌 *{d_name}* {t['tz_prayer_synced_lbl']}" if timings else ""

            card = (
                f"✅ *{t['tz_loc_detected']}*\n\n"
                f"📍 *{t['btn_city_label']}:* `{d_name or raw_text.title()}`\n"
                f"🕒 {t['btn_timezone']}: `UTC{tz_sign}{tz_detected}`\n"
                f"⏰ {t['current_time_lbl']}: `{now_str}`{prayer_sync_text}\n\n"
                f"_{t['tz_synced_hint']}_"
            )
            await update.message.reply_text(card, parse_mode="Markdown", reply_markup=get_reply_menu(user_id, context))
            return
        else:
            await update.message.reply_text(t['city_not_found'], reply_markup=get_reply_menu(user_id, context))
            return

    # HAVA DURUMU GİRİŞİ
    if mode == 'weather':
        status = await update.message.reply_text(get_text(user_id, 'weather_loading', context))
        try:
            w_res = await fetch_weather(raw_text, user_lang)
        finally:
            gc.collect()
            try:
                await status.delete()
            except Exception:
                pass
        if w_res[0]:
            name, admin1, country, c_temp, f_like, hum, wind, code, t_min, t_max = w_res
            save_user_city(user_id, name)
            card = format_weather_card(name, admin1, country, c_temp, f_like, hum, wind, code, t_min, t_max, user_lang)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(get_text(user_id, 'btn_change_weather_city', context), callback_data="change_weather_city")]
            ])
            await update.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)
            cleanup_user_temp_files(context, user_id)
            return
        await update.message.reply_text(get_text(user_id, 'weather_city_not_found', context))
        return

    # 4. MEDYA İNDİRME LİNKİ
    if is_supported_url(raw_text):
        m_url = re.search(r'https?://[^\s]+', raw_text)
        if m_url:
            clean_url = m_url.group(0).rstrip('.,!?()[]"\'')
            status = await update.message.reply_text(get_text(user_id, 'downloading', context))
            try:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    media_res = await asyncio.to_thread(download_media_sync, clean_url, tmp_dir)
                    f_path = media_res['path']
                    title = media_res['title']
                    clean_t = re.sub(r'[/\\*?:"<>|]', '', title)[:60]

                    await status.edit_text(get_text(user_id, 'uploading', context))

                    if media_res['is_photo']:
                        with open(f_path, 'rb') as f:
                            await context.bot.send_photo(chat_id=chat_id, photo=f, caption=f"📸 {clean_t}")
                    else:
                        with open(f_path, 'rb') as f:
                            context.user_data['last_video_url'] = clean_url
                            kb_audio = InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_download_audio', context), callback_data="dl_audio_extract")]])
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=f,
                                caption=f"🎬 {clean_t}",
                                duration=media_res.get('duration'),
                                width=media_res.get('width'),
                                height=media_res.get('height'),
                                supports_streaming=True,
                                reply_markup=kb_audio
                            )
            except FileTooLargeError:
                try:
                    await status.edit_text(get_text(user_id, 'error_size', context))
                except Exception:
                    pass
                status = None
            except Exception as e:
                print(f"[MEDIA_DOWNLOAD_ERROR] {e}")
                err_msg = get_text(user_id, 'video_error', context)
                try:
                    await status.edit_text(f"{err_msg}\n\n⚠️ _{safe_md(str(e)[:180])}_", parse_mode="Markdown")
                except Exception:
                    try:
                        await status.edit_text(err_msg)
                    except Exception:
                        pass
                status = None
            finally:
                if status:
                    try:
                        await status.delete()
                    except Exception:
                        pass
            return

    # 5. TO-DO MAQSAD QOʻSHISH
    if mode == 'todo_input':
        add_user_todo(user_id, raw_text)
        cleanup_user_temp_files(context, user_id)
        now = get_user_now(user_id, context)
        card_t = format_todo_card(user_id, now, user_lang)
        kb_t = build_todo_keyboard(user_id, user_lang)
        await update.message.reply_text(card_t, parse_mode="Markdown", reply_markup=kb_t)
        return

    # 6. IMTIHON QOʻSHISH
    if mode == 'exam_title_input':
        title_clean = raw_text.strip()
        context.user_data['exam_draft_title'] = title_clean
        context.user_data['mode'] = 'exam_schedule_panel'
        init_dt = (get_user_now(user_id, context) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        context.user_data['exam_draft_dt'] = init_dt
        await update.message.reply_text(format_scheduler_card(title_clean, init_dt, user_lang), parse_mode="Markdown", reply_markup=build_scheduler_keyboard(user_lang))
        return

    if mode == 'exam_schedule_panel':
        cur_dt = context.user_data.get('exam_draft_dt', get_user_now(user_id, context))
        title = context.user_data.get('exam_draft_title', get_text(user_id, 'exam_default_title', context))
        await update.message.reply_text(format_scheduler_card(title, cur_dt, user_lang), parse_mode="Markdown", reply_markup=build_scheduler_keyboard(user_lang))
        return

    # 7. DARS JADVALI GÖRSELİ
    if mode == 'schedule_img_input':
        status = await update.message.reply_text(get_text(user_id, 'schedule_processing', context))
        parsed_data = parse_schedule_text(raw_text, user_lang)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_img = os.path.join(tmp_dir, "timetable_wallpaper.png")
                generate_schedule_wallpaper(parsed_data, out_img, user_lang)
                caption_text = f"📱 {get_text(user_id, 'schedule_ready_caption', context)}"
                with open(out_img, "rb") as f_photo, open(out_img, "rb") as f_doc:
                    await update.message.reply_photo(photo=f_photo, caption=caption_text, parse_mode="Markdown")
                    await update.message.reply_document(document=f_doc, filename=f"timetable_{user_lang}.png")
            cleanup_user_temp_files(context, user_id)
        finally:
            gc.collect()
            try:
                await status.delete()
            except Exception:
                pass
        return

    # 8. HATIRLATICI GİRİŞİ
    if mode == 'remind_input':
        user_now = get_user_now(user_id, context)
        target_dt = None
        rem_text = raw_text
        if "-" in raw_text:
            rem_text, _, time_part = raw_text.partition("-")
            rem_text = rem_text.strip()
            time_part = time_part.strip()
            m_min = re.search(r"(\d+)\s*(?:daqiqa|dakika|min|m|минут)", time_part, re.IGNORECASE)
            if m_min:
                target_dt = user_now + timedelta(minutes=int(m_min.group(1)))
            else:
                m_t = re.search(r"(\d{1,2})[:.](\d{2})", time_part)
                if m_t:
                    target_dt = user_now.replace(hour=int(m_t.group(1)), minute=int(m_t.group(2)), second=0)
                    if target_dt < user_now:
                        target_dt += timedelta(days=1)

        if not target_dt:
            m_min = re.search(r"(\d+)\s*(?:daqiqa|dakika|min|m|минут)", raw_text, re.IGNORECASE)
            if m_min:
                target_dt = user_now + timedelta(minutes=int(m_min.group(1)))
                rem_text = re.sub(r"(\d+)\s*(?:daqiqa|dakika|min|m|минут)", "", raw_text, flags=re.IGNORECASE).strip()

        if target_dt:
            dt_str = target_dt.strftime("%Y-%m-%d %H:%M")
            add_user_reminder(user_id, rem_text, dt_str)
            cleanup_user_temp_files(context, user_id)
            await update.message.reply_text(f"{get_text(user_id, 'remind_saved', context)}\n\n📌 *{rem_text}*\n⏰ `{dt_str}`", parse_mode="Markdown")
        else:
            await update.message.reply_text(get_text(user_id, 'prompt_remind', context), parse_mode="Markdown")
        return

    # 9. NAMAZ VAKTİ GİRİŞİ
    if mode == 'prayer':
        res_p = await fetch_prayer_times(raw_text, user_id=user_id, user_lang=user_lang)
        timings, d_name, dt_s, h_s, src, status_type = res_p[0], res_p[1], res_p[2], res_p[3], res_p[4], res_p[5]
        if status_type == "MULTIPLE" and isinstance(d_name, list):
            candidates = d_name
            context.user_data['prayer_candidates'] = candidates
            buttons = []
            for idx, c in enumerate(candidates[:4]):
                p_label = f"📍 {c['name']}"
                if c['admin1']:
                    p_label += f" ({c['admin1']})"
                if c['country']:
                    p_label += f", {c['country']}"
                buttons.append([InlineKeyboardButton(p_label[:40], callback_data=f"sel_prayer_cand_{idx}")])
            buttons.append([InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")])
            await update.message.reply_text(
                get_text(user_id, 'loc_prompt_multimatch', context),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        if timings:
            card = format_prayer_card(d_name, timings, dt_s, h_s, src, user_lang, user_now=get_user_now(user_id, context), user_id=user_id)
            kb = get_prayer_hub_keyboard(user_id, user_lang)
            await update.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)
            cleanup_user_temp_files(context, user_id)
            return
        await update.message.reply_text(get_text(user_id, 'city_not_found', context))
        return

    # 10. ÇEVİRİ (DÜZELTME 4: SADECE mode == 'translit' İKEN ÇALIŞIR! Normal konuşmaları ASLA gasp etmez!)
    if mode == 'translit':
        lbl_latin = {'uz': "Lotin", 'tr': "Latin", 'ru': "Латиница", 'en': "Latin"}.get(user_lang, "Latin")
        lbl_cyril = {'uz': "Kirill", 'tr': "Kiril", 'ru': "Кириллица", 'en': "Cyrillic"}.get(user_lang, "Kirill")
        if is_mostly_cyrillic(raw_text):
            await update.message.reply_text(f"🔤 *{lbl_latin}:*\n\n{cyrillic_to_latin(raw_text)}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"🔤 *{lbl_cyril}:*\n\n{latin_to_cyrillic(raw_text)}", parse_mode="Markdown")
        return

    # 11. VARSAYILAN: Hiçbir modda değilse ana menü kartını göster
    await update.message.reply_text(get_text(user_id, 'menu_title', context), reply_markup=get_reply_menu(user_id, context))

# =====================================================================
# BAŞLATMA VE HATA YÖNETİCİSİ
# =====================================================================
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    err_trace = "".join(traceback.format_exception(None, err, err.__traceback__)) if err else "Bilinmeyen hata"
    add_system_log(f"UNHANDLED ERROR: {err}", level="ERROR")
    print(f"[UNHANDLED_ERROR] {err}\n{err_trace}")

    err_card = (
        "🚨 <b>NUN PROJECT // SİSTEM HATA BİLDİRİMİ</b>\n\n"
        f"⚠️ <b>Hata Türü:</b> <code>{html_lib.escape(type(err).__name__)}</code>\n"
        f"📝 <b>Mesaj:</b> <code>{html_lib.escape(str(err)[:250])}</code>\n"
        f"🕒 <b>Zaman:</b> <code>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</code>\n\n"
        f"📋 <b>Traceback:</b>\n<pre>{html_lib.escape(err_trace[-600:])}</pre>"
    )
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=aid, text=err_card, parse_mode="HTML")
        except Exception:
            pass

    if update and isinstance(update, Update) and update.effective_message:
        u_lang = get_user_lang(update.effective_user.id, context) if update.effective_user else "tr"
        err_msg = {
            'uz': "⚠️ Uzr, texnik xatolik yuz berdi. Bu haqda maʼmuriyatga xabar berildi.",
            'tr': "⚠️ Üzgünüz, geçici bir sistem hatası oluştu. Durum teknik yönetime iletildi.",
            'ru': "⚠️ Извините, произошла техническая ошибка. Администрация уведомлена.",
            'en': "⚠️ Sorry, an unexpected technical error occurred. Admins have been notified."
        }.get(u_lang, "⚠️ Geçici bir hata oluştu.")
        try:
            await update.effective_message.reply_text(err_msg)
        except Exception:
            pass

async def post_init_setup(application):
    global GLOBAL_BOT
    GLOBAL_BOT = application.bot
    asyncio.create_task(reminders_worker(application))
    asyncio.create_task(prayer_and_friday_worker(application))
    asyncio.create_task(nightly_maintenance_worker(application))
    asyncio.create_task(daily_telemetry_worker(application))

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    boot_msg = (
        f"🚀 *NUN PROJECT // SİSTEM BAŞLATILDI*\n\n"
        f"✅ *Durum:* `Tüm servisler ve veritabanı aktif`\n"
        f"⏰ *Zaman:* `{now_str}`\n"
        f"📊 *Veritabanları:* 16 dosya devrede.\n\n"
        f"_Nun Bot 7/24 kesintisiz modda hazırdır._"
    )
    for aid in ADMIN_IDS:
        try:
            await application.bot.send_message(chat_id=aid, text=boot_msg, parse_mode="Markdown")
        except Exception:
            pass
    
    default_cmds = [
        BotCommand("start", "Botni ishga tushirish / Başlat"),
        BotCommand("menu", "Asosiy menyu / Ana Menü"),
        BotCommand("reset", "Botni yangilash / Yenile & Sıfırla"),
        BotCommand("cancel", "Bekor qilish / İptal"),
    ]
    try:
        await application.bot.set_my_commands(default_cmds, scope=BotCommandScopeDefault())
    except Exception:
        pass

    for aid in ADMIN_IDS:
        try:
            u_lang = USER_LANGS.get(str(aid), 'tr')
            await update_user_bot_commands(application, aid, u_lang)
        except Exception:
            pass

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN ortam değişkeni eksik!")
    load_databases()

    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=run_keep_alive_pinger, daemon=True).start()

    app = ApplicationBuilder().token(token).post_init(post_init_setup).build()
    app.add_error_handler(global_error_handler)
    app.add_handler(TypeHandler(Update, global_user_tracker), group=-1)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler(["admin", "panel"], stats_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("admins", admins_command))
    app.add_handler(CommandHandler("addadmin", add_admin_command))
    app.add_handler(CommandHandler("deladmin", del_admin_command))
    app.add_handler(CommandHandler(["msg", "dm"], admin_msg_command))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler(["find", "search"], find_command))
    app.add_handler(CommandHandler(["logs", "log"], logs_command))
    app.add_handler(CommandHandler("sync", sync_users_command))
    app.add_handler(CommandHandler("adduser", add_user_command))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("banned", banned_command))
    app.add_handler(CommandHandler(["maintenance", "bakim"], maintenance_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Nun Bot 7/24 Kesintisiz Modda Devrede!")
    app.run_polling(drop_pending_updates=True, timeout=30)

if __name__ == "__main__":
    main()
