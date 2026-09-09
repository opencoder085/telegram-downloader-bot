import os
import re
import difflib
import json
import ssl
import shutil
import asyncio
import tempfile
import threading
import time
import struct
import urllib.parse
import urllib.request
import calendar
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import httpx
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import docx
import openpyxl
import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
import pytesseract
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
    BotCommandScopeChat,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# =====================================================================
# RENDER 7/24 SAĞLIK SUNUCUSU & SELF-PINGER
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"status": "ok", "service": "NUN_BOT_24_7"}')

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
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) NunBot-KeepAlive/2.0'}
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    print(f"[KEEP-ALIVE] Ping basarili: {target_url} -> Kod: {resp.status}")
            except Exception as e:
                print(f"[KEEP-ALIVE] Ping uyarisi: {e}")
        else:
            print("[KEEP-ALIVE] RENDER_EXTERNAL_HOSTNAME tanimli degil.")
        time.sleep(480)

# =====================================================================
# GELİŞMİŞ VE KAPSAMLI SAAT DİLİMİ & KONUM MOTORU
# =====================================================================
CITY_TIMEZONE_MAP = {
    # O'zbekiston (UTC+5)
    "toshkent": 5, "tashkent": 5, "samarqand": 5, "samarkand": 5, "buxoro": 5, "bukhara": 5,
    "andijon": 5, "andijan": 5, "namangan": 5, "fargona": 5, "fergana": 5, "qoqon": 5, "kokand": 5,
    "urganch": 5, "urgench": 5, "nukus": 5, "qarshi": 5, "karshi": 5, "navoiy": 5, "navoi": 5,
    "termiz": 5, "termez": 5, "guliston": 5, "gulistan": 5, "jizzax": 5, "jizzakh": 5,
    "xiva": 5, "khiva": 5, "margilon": 5, "margilan": 5, "angren": 5, "chirchiq": 5, "chirchik": 5,
    "olmaliq": 5, "almalyk": 5, "shahrisabz": 5, "denov": 5, "zarafshon": 5, "bekobod": 5,

    # Turkiya (UTC+3)
    "istanbul": 3, "ankara": 3, "izmir": 3, "bursa": 3, "antalya": 3, "konya": 3, "adana": 3,
    "gaziantep": 3, "sanliurfa": 3, "kocaeli": 3, "mersin": 3, "diyarbakir": 3, "hatay": 3,
    "manisa": 3, "kayseri": 3, "samsun": 3, "balikesir": 3, "kahramanmaras": 3, "van": 3,
    "aydin": 3, "denizli": 3, "sakarya": 3, "erzurum": 3, "mugla": 3, "eskisehir": 3, "trabzon": 3,
    "elazig": 3, "sivas": 3, "batman": 3, "rize": 3, "malatya": 3, "tekirdag": 3, "canakkale": 3,

    # Rossiya
    "moskva": 3, "moscow": 3, "piter": 3, "sankt-peterburg": 3, "spb": 3, "kazan": 3, "sochi": 3,
    "krasnodar": 3, "nizhny novgorod": 3, "samara": 4, "ufa": 5, "yekaterinburg": 5, "ekaterinburg": 5,
    "chelyabinsk": 5, "tyumen": 5, "omsk": 6, "novosibirsk": 7, "krasnoyarsk": 7, "irkutsk": 8,
    "yakutsk": 9, "vladivostok": 10,

    # Markaziy Osiyo & Kavkaz
    "almaty": 5, "astana": 5, "shymkent": 5, "bishkek": 6, "osh": 6, "dushanbe": 5,
    "ashgabat": 5, "baku": 4, "tbilisi": 4, "yerevan": 4,

    # Yaqin Sharq & Ko'rfaz
    "dubai": 4, "dubay": 4, "abu dhabi": 4, "riyadh": 3, "makka": 3, "makkah": 3, "mecca": 3,
    "madina": 3, "medina": 3, "doha": 3, "kuwait": 3, "muscat": 4, "tehran": 3,

    # Yevropa
    "london": 0, "dublin": 0, "lisbon": 0, "paris": 1, "parij": 1, "berlin": 1, "rome": 1, "rim": 1,
    "madrid": 1, "amsterdam": 1, "brussels": 1, "bryussel": 1, "vienna": 1, "vena": 1, "warsaw": 1,
    "varshava": 1, "prague": 1, "praqa": 1, "budapest": 1, "kyiv": 2, "kiev": 2, "athens": 2,
    "afina": 2, "bucharest": 2, "buxarest": 2, "helsinki": 2, "stockholm": 1, "oslo": 1,

    # Osiyo
    "seoul": 9, "seul": 9, "tokyo": 9, "beijing": 8, "pekin": 8, "shanghai": 8, "hong kong": 8,
    "singapore": 8, "singapur": 8, "kuala lumpur": 8, "bangkok": 7, "jakarta": 7, "delhi": 5, "mumbai": 5,

    # Amerika
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
    if 37.0 <= lat <= 46.0 and 56.0 <= lon <= 73.5: return 5  # O'zbekiston
    if 35.5 <= lat <= 42.5 and 25.5 <= lon <= 45.0: return 3  # Turkiya
    if 50.0 <= lat <= 65.0 and 28.0 <= lon <= 55.0: return 3  # Moskva / Yevropa Rossiyasi
    if 22.0 <= lat <= 27.5 and 51.5 <= lon <= 60.0: return 4  # BAA / Dubay
    if 16.0 <= lat <= 32.0 and 34.0 <= lon <= 51.5: return 3  # Saudiya
    if 30.0 <= lat <= 46.0 and 124.0 <= lon <= 146.0: return 9 # Koreya / Yaponiya
    if 1.0 <= lat <= 54.0 and 97.0 <= lon <= 124.0: return 8   # Xitoy / Singapur
    if 36.5 <= lat <= 43.5 and 67.0 <= lon <= 75.0: return 5   # Tojikiston
    if 39.0 <= lat <= 43.5 and 69.0 <= lon <= 80.5: return 6   # Qirg'iziston
    if 40.5 <= lat <= 55.5 and 46.5 <= lon <= 87.5: return 5   # Qozog'iston
    if 49.0 <= lat <= 60.5 and -11.0 <= lon <= 2.0: return 0   # Buyuk Britaniya
    if 36.0 <= lat <= 55.0 and 2.0 <= lon <= 24.0: return 1    # Markaziy Yevropa
    if 34.0 <= lat <= 70.0 and 20.0 <= lon <= 35.0: return 2   # Sharqiy Yevropa
    if 24.0 <= lat <= 50.0:
        if -80.0 <= lon <= -65.0: return -5
        if -90.0 <= lon <= -80.0: return -5
        if -105.0 <= lon <= -90.0: return -6
        if -115.0 <= lon <= -105.0: return -7
        if -125.0 <= lon <= -115.0: return -8
    return max(-12, min(14, round(lon / 15.0)))

def parse_tz_from_text(text: str):
    m = re.search(r"(?:(?:utc|gmt)\s*)?([+-]\d{1,2})\b", text, re.IGNORECASE)
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

USER_LANGS = {}
USER_TIMEZONES = {}
USER_TZ_LOCKED = {}
USER_EXAMS = {}
USER_REMINDERS = {}

def load_databases():
    global USER_LANGS, USER_TIMEZONES, USER_TZ_LOCKED, USER_EXAMS, USER_REMINDERS
    for fname, var_ref in [
        (LANG_FILE, USER_LANGS),
        (TZ_FILE, USER_TIMEZONES),
        (TZ_LOCK_FILE, USER_TZ_LOCKED),
        (EXAMS_FILE, USER_EXAMS),
        (REMINDERS_FILE, USER_REMINDERS)
    ]:
        if os.path.exists(fname):
            try:
                with open(fname, "r", encoding="utf-8") as f:
                    var_ref.update(json.load(f))
            except Exception:
                pass

def save_json(fname, data):
    try:
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

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

def silent_background_tz_sync(user_id: int, raw_text: str = None, user_lang_code: str = None):
    uid_str = str(user_id)
    if USER_TZ_LOCKED.get(uid_str):
        return USER_TIMEZONES.get(uid_str, 3)

    detected_tz = None

    # 1. Metin icerisinde saat dilimi veya sehir taramasi
    if raw_text:
        m_tz = re.search(r"(?:(?:utc|gmt)\s*)?([+-]\d{1,2})\b", raw_text, re.IGNORECASE)
        if m_tz:
            val = int(m_tz.group(1))
            if -12 <= val <= 14:
                detected_tz = val

        if detected_tz is None:
            text_clean = re.sub(r"['’`ʻʼ\-]", "", raw_text.lower()).replace("i̇", "i").replace("ı", "i")
            for city, tz in CITY_TIMEZONE_MAP.items():
                pattern = r"\b" + re.escape(city) + r"(?:daman|dasan|damiz|dayam|dayik|da|de|dan|den|ga|ge|ya|ye|a|e|ni|ning|lik|li)?\b"
                if re.search(pattern, text_clean):
                    detected_tz = tz
                    break

    # 2. Telegram arayuz dili fallback
    if detected_tz is None and uid_str not in USER_TIMEZONES and user_lang_code:
        code = user_lang_code.lower()
        if code.startswith('tr'): detected_tz = 3
        elif code.startswith('ru'): detected_tz = 3
        elif code.startswith('uz'): detected_tz = 5

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

def add_user_reminder(user_id, text: str, target_dt_str: str) -> str:
    uid_str = str(user_id)
    if uid_str not in USER_REMINDERS:
        USER_REMINDERS[uid_str] = []
    rem_id = str(int(time.time() * 1000))[-6:]
    USER_REMINDERS[uid_str].append({'id': rem_id, 'text': text, 'time': target_dt_str})
    save_json(REMINDERS_FILE, USER_REMINDERS)
    return rem_id

def get_user_reminders(user_id) -> list:
    return USER_REMINDERS.get(str(user_id), [])

def delete_user_reminder(user_id, rem_id: str):
    uid_str = str(user_id)
    if uid_str in USER_REMINDERS:
        USER_REMINDERS[uid_str] = [r for r in USER_REMINDERS[uid_str] if r.get('id') != rem_id]
        save_json(REMINDERS_FILE, USER_REMINDERS)

def cleanup_user_temp_files(context, user_id):
    if not context or not context.user_data:
        return
    dp = context.user_data.get('direct_file_path')
    if dp:
        try:
            if os.path.exists(dp): os.remove(dp)
        except Exception: pass
    context.user_data.pop('direct_file_path', None)
    context.user_data.pop('exam_draft_title', None)
    context.user_data.pop('exam_draft_dt', None)
    context.user_data['mode'] = 'auto'

# =====================================================================
# PDF DÖNÜŞTÜRME MOTORU (WORD, EXCEL, TXT, RESİM ➔ PDF)
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

    font_path_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font_path_norm = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    try:
        font_large = ImageFont.truetype(font_path_bold, 44)
        font_med = ImageFont.truetype(font_path_bold, 30)
        font_sub = ImageFont.truetype(font_path_norm, 24)
        font_item = ImageFont.truetype(font_path_norm, 26)
    except Exception:
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

# =====================================================================
# POMODORO & HATIRLATICI MOTORU (GERÇEK KULLANICI YEREL SAATİ)
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
# ÖZBEKÇE KİRİL <-> LATİN ÇEVİRİ MOTORU
# =====================================================================
APOSTROPHES = set(["'", "\u2019", "\u2018", "`", "\u02bb", "\u02bc"])
VOWELS_CYR = set("аоуиэеёюяўАОУИЭЕЁЮЯЎ")

MAP_CYR_TO_LAT = {
    '\u0410': 'A', '\u0430': 'a', '\u0411': 'B', '\u0431': 'b', '\u0412': 'V', '\u0432': 'v',
    '\u0413': 'G', '\u0433': 'g', '\u0414': 'D', '\u0434': 'd', '\u0416': 'J', '\u0436': 'j',
    '\u0417': 'Z', '\u0437': 'z', '\u0418': 'I', '\u0438': 'i', '\u0419': 'Y', '\u0439': 'y',
    '\u041a': 'K', '\u043a': 'k', '\u049a': 'Q', '\u049b': 'q', '\u041b': 'L', '\u043b': 'l',
    '\u041c': 'M', '\u043c': 'm', '\u041d': 'N', '\u043d': 'n', '\u041e': 'O', '\u043e': 'o',
    '\u041f': 'P', '\u043f': 'p', '\u0420': 'R', '\u0440': 'r', '\u0421': 'S', '\u0441': 's',
    '\u0422': 'T', '\u0442': 't', '\u0423': 'U', '\u0443': 'u', '\u0424': 'F', '\u0444': 'f',
    '\u0425': 'X', '\u0445': 'x', '\u04b2': 'H', '\u04b3': 'h', '\u042d': 'E', '\u044d': 'e',
}

MAP_LAT_TO_CYR = {
    'A': '\u0410', 'a': '\u0430', 'B': '\u0411', 'b': '\u0431', 'V': '\u0412', 'v': '\u0432',
    'G': '\u0413', 'g': '\u0433', 'D': '\u0414', 'd': '\u0434', 'J': '\u0416', 'j': '\u0436',
    'Z': '\u0417', 'z': '\u0437', 'I': '\u0418', 'i': '\u0438', 'Y': '\u0419', 'y': '\u0439',
    'K': '\u041a', 'k': '\u043a', 'Q': '\u049a', 'q': '\u049b', 'L': '\u041b', 'l': '\u043b',
    'M': '\u041c', 'm': '\u043c', 'N': '\u041d', 'n': '\u043d', 'O': '\u041e', 'o': '\u043e',
    'P': '\u041f', 'p': '\u043f', 'R': '\u0420', 'r': '\u0440', 'S': '\u0421', 's': '\u0441',
    'T': '\u0422', 't': '\u0442', 'U': '\u0423', 'u': '\u0443', 'F': '\u0424', 'f': '\u0444',
    'X': '\u0425', 'x': '\u0445', 'H': '\u04b2', 'h': '\u04b3',
}

def cyrillic_to_latin(text: str) -> str:
    res = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        prev = text[i-1] if i > 0 else " "
        nxt = text[i+1] if i+1 < n else ""
        if c in ('С', 'с') and nxt in ('Ҳ', 'ҳ'):
            res.append("S'H" if c.isupper() and nxt.isupper() else ("S'h" if c.isupper() else "s'h"))
            i += 2; continue
        if c in ('Е', 'е'):
            if i == 0 or not prev.isalpha() or prev in VOWELS_CYR or prev in 'ъЪьЬ':
                res.append("YE" if c.isupper() and nxt.isupper() else ("Ye" if c.isupper() else "ye"))
            else:
                res.append("E" if c.isupper() else "e")
            i += 1; continue
        if c in ('Ё', 'ё'): res.append("Yo" if c.isupper() else "yo"); i += 1; continue
        if c in ('Ю', 'ю'): res.append("Yu" if c.isupper() else "yu"); i += 1; continue
        if c in ('Я', 'я'): res.append("Ya" if c.isupper() else "ya"); i += 1; continue
        if c in ('Ch', 'ч'): res.append("Ch" if c.isupper() else "ch"); i += 1; continue
        if c in ('Ш', 'ш', 'Щ', 'щ'): res.append("Sh" if c.isupper() else "sh"); i += 1; continue
        if c in ('Ц', 'ц'): res.append("Ts" if c.isupper() else "ts"); i += 1; continue
        if c == 'Ў': res.append("Oʻ"); i += 1; continue
        if c == 'ў': res.append("oʻ"); i += 1; continue
        if c == 'Ғ': res.append("Gʻ"); i += 1; continue
        if c == 'ғ': res.append("gʻ"); i += 1; continue
        if c in ('Ъ', 'ъ'): res.append("'"); i += 1; continue
        if c in ('Ь', 'ь'): i += 1; continue
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
            res.append("СҲ" if nxt2.isupper() else "Сҳ" if c == 'S' else "сҳ"); i += 3; continue
        if c in ('o', 'O') and nxt and nxt in APOSTROPHES:
            res.append("Ў" if c == 'O' else "ў"); i += 2; continue
        if c in ('g', 'G') and nxt and nxt in APOSTROPHES:
            res.append("Ғ" if c == 'G' else "ғ"); i += 2; continue
        if c in ('s', 'S') and nxt in ('h', 'H'):
            res.append("Ш" if c.isupper() else "ш"); i += 2; continue
        if c in ('c', 'C') and nxt in ('h', 'H'):
            res.append("Ch" if c.isupper() else "ch"); i += 2; continue
        if c in ('t', 'T') and nxt in ('s', 'S'):
            res.append("Ц" if c.isupper() else "ц"); i += 2; continue
        if c in ('y', 'Y') and nxt in ('o', 'O'): res.append("Ё" if c.isupper() else "ё"); i += 2; continue
        if c in ('y', 'Y') and nxt in ('u', 'U'): res.append("Ю" if c.isupper() else "ю"); i += 2; continue
        if c in ('y', 'Y') and nxt in ('a', 'A'): res.append("Я" if c.isupper() else "я"); i += 2; continue
        if c in ('y', 'Y') and nxt in ('e', 'E'): res.append("Е" if c.isupper() else "е"); i += 2; continue
        if c in ('e', 'E'):
            res.append("Э" if (i == 0 or not prev.isalpha() or prev.lower() in 'aouie') else "Е" if c == 'E' else "е")
            i += 1; continue
        if c in APOSTROPHES:
            res.append("ъ" if prev.isalpha() else "'"); i += 1; continue
        res.append(MAP_LAT_TO_CYR.get(c, c))
        i += 1
    return "".join(res)

def is_mostly_cyrillic(text: str) -> bool:
    return len(re.findall(r'[\u0400-\u04FF]', text)) >= len(re.findall(r'[a-zA-Z]', text))

# =====================================================================
# NAMAZ VAKİTLERİ MOTORU
# =====================================================================
UZ_REGIONS = {
    "toshkent": "toshkent", "samarqand": "samarqand-shahri", "buxoro": "buxoro-shahri",
    "andijon": "andijon-shahri", "namangan": "namangan-shahri", "fargona": "fargona-shahri",
    "qoqon": "qoqon-shahri", "urganch": "urganch-shahri", "nukus": "nukus-shahri",
    "qarshi": "qarshi-shahri", "navoiy": "navoiy-shahri", "termiz": "termiz-shahri",
}

def clean_time_str(val: str) -> str:
    m = re.search(r"\d{1,2}:\d{2}", str(val))
    return m.group(0) if m else "--:--"

async def fetch_prayer_times(city_input: str, user_id: int = None):
    c_norm = re.sub(r"['’`ʻʼ]", "", city_input.lower().strip()).replace('i̇', 'i').replace('ı', 'i')
    headers = {"User-Agent": "Mozilla/5.0"}

    # Otomatik saat dilimi eşleştirme
    detected_tz = resolve_tz_from_city(c_norm)
    if user_id and detected_tz is not None:
        save_user_timezone(user_id, detected_tz)

    # 1. Özbekistan Şehirleri
    slug = UZ_REGIONS.get(c_norm)
    if not slug:
        matches = difflib.get_close_matches(c_norm, list(UZ_REGIONS.keys()), n=1, cutoff=0.75)
        if matches: slug = UZ_REGIONS[matches[0]]
    if slug:
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False, follow_redirects=True) as client:
                resp = await client.get(f"https://namoz-vaqti.uz/index.php?format=json&region={slug}", headers=headers)
                if resp.status_code == 200:
                    j = resp.json()
                    t = j.get("today", {}).get("times", {})
                    meta = j.get("meta", {})
                    return {
                        "Fajr": t.get("bomdod"), "Sunrise": t.get("quyosh"), "Dhuhr": t.get("peshin"),
                        "Asr": t.get("asr"), "Maghrib": t.get("shom"), "Isha": t.get("xufton")
                    }, meta.get("region", {}).get("name", city_input.title()), meta.get("date", ""), "", "Oʻzbekiston Din ishlari qoʻmitasi"
        except Exception: pass

    # 2. Küresel Şehirler
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False, follow_redirects=True) as client:
            resp = await client.get(f"https://api.aladhan.com/v1/timingsByAddress?address={urllib.parse.quote(city_input)}", headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                d = data.get("date", {})
                return data.get("timings", {}), city_input.title(), d.get("readable", ""), d.get("hijri", {}).get("date", ""), "AlAdhan API"
    except Exception: pass

    return None, None, None, None, None

def format_prayer_card(display_name: str, timings: dict, date_str: str, hijri_str: str, source: str, lang: str = 'uz'):
    t_f = clean_time_str(timings.get("Fajr"))
    t_s = clean_time_str(timings.get("Sunrise"))
    t_d = clean_time_str(timings.get("Dhuhr"))
    t_a = clean_time_str(timings.get("Asr"))
    t_m = clean_time_str(timings.get("Maghrib"))
    t_i = clean_time_str(timings.get("Isha"))

    if lang == 'tr':
        lbls = ("İMSAK", "GÜNEŞ", "ÖĞLE", "İKİNDİ", "AKŞAM", "YATSI")
        hdr = "*NUN PROJECT // NAMAZ VAKİTLERİ*"
    elif lang == 'ru':
        lbls = ("ФАДЖР", "ВОСХОД", "ЗУХР", "АСР", "МАГРИБ", "ИША")
        hdr = "*NUN PROJECT // ВРЕМЯ НАМАЗА*"
    elif lang == 'en':
        lbls = ("FAJR", "SUNRISE", "DHUHR", "ASR", "MAGHRIB", "ISHA")
        hdr = "*NUN PROJECT // PRAYER TIMES*"
    else:
        lbls = ("BOMDOD", "QUYOSH", "PESHIN", "ASR", "SHOM", "XUFTON")
        hdr = "*NUN PROJECT // NAMOZ VAQTLARI*"

    return (
        f"{hdr}\n"
        f"📍 *[ {display_name.upper()} ]*\n"
        f"📅 `{date_str}`\n\n"
        f"┌────────────────────────────┐\n"
        f"  ▫️ *{lbls[0]}:*    `{t_f}`\n"
        f"  ▫️ *{lbls}:*    `{t_s}`\n"
        f"  ▫️ *{lbls}:*    `{t_d}`\n"
        f"  ▫️ *{lbls}:*    `{t_a}`\n"
        f"  ▫️ *{lbls}:*    `{t_m}`\n"
        f"  ▫️ *{lbls[5]}:*    `{t_i}`\n"
        f"└────────────────────────────┘\n"
        f"_{source}_"
    )

# =====================================================================
# ÇOK DİLLİ CANLI İNTERAKTİF ZAMANLAYICI PANELİ & TAKVİM
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
        f"📌 {lbl_exam}: *{clean_title}*\n\n"
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
            'today': "⚡ Сегодня", 'tmrw': "⚡ Завtra", 'week': "⚡ 1 неделя",
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
            await message.edit_text(text.replace("*", "").replace("_", "").replace("`", ""), reply_markup=reply_markup)
        except Exception:
            pass

# =====================================================================
# SADELEŞTİRİLMİŞ ARAYÜZLER (PDF, POMODORO, ZİKİRLER, SAAT DİLİMİ)
# =====================================================================
def get_pdf_hub_keyboard(lang: str = 'uz'):
    t = TEXTS.get(lang, TEXTS['uz'])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t['pdf_hub_to_pdf_btn'], callback_data="pdf_act_to_pdf")],
        [InlineKeyboardButton(t['pdf_hub_ocr_btn'], callback_data="pdf_act_ocr")],
    ])

def get_pomodoro_keyboard(user_id: int, lang: str = 'uz'):
    t = TEXTS.get(lang, TEXTS['uz'])
    tz_off = get_user_tz_offset(user_id)
    tz_btn_lbl = f"🕒 UTC+{tz_off}" if tz_off >= 0 else f"🕒 UTC{tz_off}"
    cur_time = get_user_now(user_id).strftime("%H:%M")

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t['pomo_btn_work25'], callback_data="pomo_25"), InlineKeyboardButton(t['pomo_btn_break5'], callback_data="pomo_5")],
        [InlineKeyboardButton(t['pomo_btn_work50'], callback_data="pomo_50"), InlineKeyboardButton(t['pomo_btn_break10'], callback_data="pomo_10")],
        [InlineKeyboardButton(t['pomo_btn_add_remind'], callback_data="remind_add"), InlineKeyboardButton(t['pomo_btn_my_reminds'], callback_data="remind_list")],
        [InlineKeyboardButton(f"{t['btn_timezone']}: {tz_btn_lbl} ({cur_time})", callback_data="open_tz_selector")]
    ])

def get_adhkar_selection_keyboard(lang: str = 'uz'):
    t = TEXTS.get(lang, TEXTS['uz'])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t['adhkar_morning_btn'], callback_data="adhkar_morning"), InlineKeyboardButton(t['adhkar_evening_btn'], callback_data="adhkar_evening")]
    ])

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"), InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")],
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
# 4 DİLLİ TAM VE EKSİKSİZ SÖZLÜK
# =====================================================================
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Nun Botga xush kelibsiz.\nQuyidagi menyudan kerakli boʻlimni tanlang:",
        'menu_title': "📋 Asosiy menyu:",
        'btn_video': "🎬 Video yuklash",
        'btn_prayer': "🕌 Namoz vaqtlari",
        'btn_pdf_hub': "📄 PDF & Hujjatlar",
        'btn_exam': "🎓 Imtihon & Taymer",
        'btn_schedule_img': "🗓️ Dars Jadvali (Rasm)",
        'btn_pomodoro': "⏱️ Pomodoro & Eslatma",
        'btn_adhkar': "📿 Zikrlar",
        'btn_translit': "🔤 Kirill ⇄ Lotin",
        'btn_lang': "🌐 Tilni tanlash",
        'btn_timezone': "🕒 Vaqt mintaqasi",
        'btn_auto_loc': "📍 Avtomatik aniqlash (Joylashuv / Shahar)",
        'prompt_video': "🔗 Instagram, TikTok, Facebook yoki X (Twitter) havolasini yuboring:",
        'prompt_prayer': "🕌 *NUN PROJECT // NAMOZ VAQTLARI*\n\nNamoz vaqtlarini bilmoqchi boʻlgan shahar nomini yozib yuboring:\n_(Masalan: *Qoʻqon*, *Toshkent*, *Samarqand*, *Istanbul*...)_",
        'prompt_pdf_hub': "📄 *NUN PROJECT // PDF & HUJJATLAR MARKAZI*\n\nAmalni tanlang:",
        'prompt_schedule_img': "🗓️ *DARS JADVALI RASMI*\n\nDars jadvalingizni kunlar boʻyicha yozib yuboring (Masalan: Dushanba: 09:00 Matematika...):\nBot uni 1080x1920 qulflangan ekran formatiga aylantiradi.",
        'prompt_pomodoro': "⏱️ *POMODORO & ESLATMA MARKAZI*",
        'prompt_adhkar': "📿 Zikr turini tanlang:",
        'prompt_translit': "✍️ Matningizni yuboring, avtomatik Kirill ⇄ Lotin oʻgirib beraman:",
        'prompt_convert_to_pdf': "📸 *PDF GA OʻGIRISH REJIMI FAOL*\n\nPDF formatiga oʻtkazmoqchi boʻlgan faylni yuboring:\n_(Rasm, Word .docx, Excel .xlsx yoki TXT)_",
        'prompt_ocr': "🔍 *RASMDAN MATN CHIQARISH (OCR) FAOL*\n\nMatnini oʻqib olmoqchi boʻlgan kitob yoki taxta rasmini yuboring:\n_(Arabcha, Xitoycha, Ruscha, Oʻzbekcha va barcha tillar qoʻllab-quvvatlanadi)_",
        'prompt_exam_title': "🎓 *IMTIHON QOʻSHISH*\n\n✍️ Imtihon yoki fanning nomini yozib yuboring:\n_(Masalan: *Oliy Matematika*, *Fizika Final*)_",
        'prompt_remind': "⏰ Eslatmani quyidagi formatda yuboring:\n`Kitob o'qish - 18:30` yoki `Dars - 30 daqiqa`",
        'prompt_timezone': "🕒 *VAQT MINTAQASINI TANLANG*\n\nOʻzingiz joylashgan shahar yoki vaqt mintaqasini tanlang (Taymer va eslatmalar aniq ishlashi uchun):",
        'prompt_send_location': "📍 *JOY LASHUV / SHAHARNI YUBORING*\n\nIltimos, Telegram orqali joylashuvingizni (Location) yuboring yoki shahar nomini yozing (Masalan: *Toshkent*, *Istanbul*, *Moskva*, *London*...):",
        'tz_loc_detected': "JOY LASHUV VA VAQT ANIQLANDI",
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
        'downloading': "Video yuklab olinmoqda, iltimos kuting...",
        'uploading': "Telegramga yuklanmoqda...",
        'error_size': "Fayl hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "Xatolik yuz berdi. Qaytadan urinib koʻring.",
        'schedule_processing': "Qulflangan ekran fon rasmi tayyorlanmoqda...",
        'schedule_ready_caption': "Dars jadvali (Qulflangan ekran)",
        'doc_processing': "Fayl qabul qilindi, ishlov berilmoqda...",
        'video_error': "Videoni yuklab olishda xatolik yuz berdi.",
        'adhkar_morning_btn': "🌅 Tonggi zikrlar",
        'adhkar_evening_btn': "🌇 Kechki zikrlar",
        'adhkar_morning_text': (
            "🌅 *TONGGI ZIKRLAR*\n\n"
            "1. *Oyatul Kursiy*\n"
            "2. *Ixlos, Falaq, Nos suralari* (3 marta)\n"
            "3. *«Asbahnaa va asbahal mulku lillaah, valhamdu lillaah, laa ilaaha illallohu vahdahu laa shariyka lah...»*\n"
            "4. *«Allohumma bika asbahnaa va bika amsaynaa va bika nahyaa va bika namuutu va ilaykan nushuur.»*\n"
            "5. *«Subhanallohi va bihamdih»* (100 marta)"
        ),
        'adhkar_evening_text': (
            "🌇 *KECHKI ZIKRLAR*\n\n"
            "1. *Oyatul Kursiy*\n"
            "2. *Ixlos, Falaq, Nos suralari* (3 marta)\n"
            "3. *«Amsaynaa va amsal mulku lillaah, valhamdu lillaah...»*\n"
            "4. *«Allohumma bika amsaynaa va bika asbahnaa va bika nahyaa va bika namuutu va ilaykal masiyr.»*\n"
            "5. *«A'uuzu bi kalimaatillaahit taammaati min sharri maa xolaq»* (3 marta)"
        )
    },
    'tr': {
        'welcome': "Merhaba! Nun Bot'a hoş geldiniz.\nAşağıdaki menüden işlem seçiniz:",
        'menu_title': "📋 Ana Menü:",
        'btn_video': "🎬 Video İndir",
        'btn_prayer': "🕌 Namaz Vakitleri",
        'btn_pdf_hub': "📄 PDF & Belge Araçları",
        'btn_exam': "🎓 Sınav & Geri Sayım",
        'btn_schedule_img': "🗓️ Ders Programı (Görsel)",
        'btn_pomodoro': "⏱️ Pomodoro & Hatırlatıcı",
        'btn_adhkar': "📿 Zikirler",
        'btn_translit': "🔤 Kiril ⇄ Latin",
        'btn_lang': "🌐 Dil Seçimi",
        'btn_timezone': "🕒 Saat Dilimi",
        'btn_auto_loc': "📍 Otomatik Algıla (Konum / Şehir)",
        'prompt_video': "🔗 Instagram, TikTok, Facebook veya X (Twitter) linki gönderin:",
        'prompt_prayer': "🕌 *NUN PROJECT // NAMAZ VAKİTLERİ*\n\nNamaz vakitlerini öğrenmek istediğiniz şehrin adını yazıp gönderin:\n_(Örneğin: *Kokand*, *İstanbul*, *Ankara*, *Taşkent*...)_",
        'prompt_pdf_hub': "📄 *NUN PROJECT // PDF & BELGE ARAÇLARI*\n\nİşlem seçiniz:",
        'prompt_schedule_img': "🗓️ *HAFTALIK DERS PROGRAMI GÖRSELİ*\n\nDers programınızı gün gün yazıp gönderin (Örn: Pazartesi: 09:00 Matematik...):\nBot 1080x1920 telefon kilit ekranı formatına dönüştürecektir.",
        'prompt_pomodoro': "⏱️ *POMODORO & HATIRLATICI MERKEZİ*",
        'prompt_adhkar': "📿 Zikir türünü seçiniz:",
        'prompt_translit': "✍️ Metninizi gönderin, otomatik Kiril ⇄ Latin alfabesine dönüştüreyim:",
        'prompt_convert_to_pdf': "📸 *PDF DÖNÜŞTÜRÜCÜ AKTİF*\n\nPDF formatına dönüştürmek istediğiniz dosyayı gönderin:\n_(Fotoğraf, Word .docx, Excel .xlsx veya TXT)_",
        'prompt_ocr': "🔍 *GÖRSELDEN METİN ÇIKARMA (OCR) AKTİF*\n\nMetnini okutmak istediğiniz kitap veya tahta fotoğrafını gönderin:\n_(Arapça, Çince, Rusça, Türkçe, Özbekçe ve tüm diller desteklenir)_",
        'prompt_exam_title': "🎓 *SINAV EKLE*\n\n✍️ Sınav veya dersin adını yazıp gönderin:\n_(Örneğin: *Yüksek Matematik*, *Fizik Final*)_",
        'prompt_remind': "⏰ Hatırlatıcıyı şu formatta gönderin:\n`Kitap oku - 18:30` veya `Ders - 30 dakika`",
        'prompt_timezone': "🕒 *SAAT DİLİMİ SEÇİMİ*\n\nBulunduğunuz şehir veya saat dilimini seçiniz (Zamanlayıcı ve hatırlatıcıların tam vaktinde çalışması için):",
        'prompt_send_location': "📍 *KONUM / ŞEHİR BİLGİSİ*\n\nLütfen Telegram üzerinden konumunuzu (Location) gönderin veya şehrinizi yazın (Örn: *İstanbul*, *Taşkent*, *Ankara*, *Moskova*...):",
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
        'downloading': "Medya indiriliyor, lütfen bekleyin...",
        'uploading': "Telegram'a yükleniyor...",
        'error_size': "Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "Bir hata oluştu. Lütfen tekrar deneyin.",
        'schedule_processing': "Kilit ekranı duvar kağıdı hazırlanıyor...",
        'schedule_ready_caption': "Ders Programı (Kilit Ekranı)",
        'doc_processing': "Dosya alındı, işleniyor...",
        'video_error': "Video indirilirken bir hata oluştu.",
        'adhkar_morning_btn': "🌅 Sabah Zikirleri",
        'adhkar_evening_btn': "🌇 Akşam Zikirleri",
        'adhkar_morning_text': (
            "🌅 *SABAH ZİKİRLERİ*\n\n"
            "1. *Ayet-el Kürsi*\n"
            "2. *İhlas, Felak, Nas Sureleri* (3 defa)\n"
            "3. *«Esbahnâ ve esbaha'l-mülkü lillâh, vel-hamdü lillâh...»*\n"
            "4. *«Allâhümme bike esbahnâ ve bike emseynâ ve bike nehyâ ve bike nemûtü ve ileyke'n-nüşûr.»*\n"
            "5. *«Sübhânallâhi ve bi-hamdihî»* (100 defa)"
        ),
        'adhkar_evening_text': (
            "🌇 *AKŞAM ZİKİRLERİ*\n\n"
            "1. *Ayet-el Kürsi*\n"
            "2. *İhlas, Felak, Nas Sureleri* (3 defa)\n"
            "3. *«Emseynâ ve emse'l-mülkü lillâh, vel-hamdü lillâh...»*\n"
            "4. *«Allâhümme bike emseynâ ve bike esbahnâ ve bike nehyâ ve bike nemûtü ve ileyke'l-masîr.»*\n"
            "5. *«Eûzü bi-kelimâtillâhi't-tâmmâti min şerri mâ halak.»* (3 defa)"
        )
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать в Nun Bot.\nВыберите действие в меню:",
        'menu_title': "📋 Главное меню:",
        'btn_video': "🎬 Скачать видео",
        'btn_prayer': "🕌 Время намаза",
        'btn_pdf_hub': "📄 PDF & Документы",
        'btn_exam': "🎓 Экзамены и Таймер",
        'btn_schedule_img': "🗓️ Расписание (Фото)",
        'btn_pomodoro': "⏱️ Помодоро & Напоминания",
        'btn_adhkar': "📿 Зикры",
        'btn_translit': "🔤 Кириллица ⇄ Латиница",
        'btn_lang': "🌐 Сменить язык",
        'btn_timezone': "🕒 Часовой пояс",
        'btn_auto_loc': "📍 Автоопределение (Гео / Город)",
        'prompt_video': "🔗 Отправьте ссылку из Instagram, TikTok, Facebook или X (Twitter):",
        'prompt_prayer': "🕌 *NUN PROJECT // ВРЕМЯ НАМАЗА*\n\nНапишите название города:\n_(Например: *Коканд*, *Ташкент*, *Москва*, *Стамбул*...)_",
        'prompt_pdf_hub': "📄 *NUN PROJECT // PDF & ДОКУМЕНТЫ*\n\nВыберите действие:",
        'prompt_schedule_img': "🗓️ *РАСПИСАНИЕ ЗАНЯТИЙ (ОБОИ)*\n\nОтправьте расписание по дням (Напр: Понедельник: 09:00 Математика...):\nБот создаст стильные обои 1080x1920 для экрана блокировки.",
        'prompt_pomodoro': "⏱️ *ПОМОДОРО И НАПОМИНАНИЯ*",
        'prompt_adhkar': "📿 Выберите категорию зикров:",
        'prompt_translit': "✍️ Отправьте текст, автоматически переведу Кириллица ⇄ Латиница:",
        'prompt_convert_to_pdf': "📸 *КОНВЕРТЕР В PDF АКТИВЕН*\n\nОтправьте файл для конвертации в PDF:\n_(Фото, Word .docx, Excel .xlsx или TXT)_",
        'prompt_ocr': "🔍 *ИЗВЛЕЧЕНИЕ ТЕКСТА (OCR) АКТИВНО*\n\nОтправьте фото книги, конспекта или доски:\n_(Поддерживаются арабский, китайский, русский, узбекский, английский и все языки)_",
        'prompt_exam_title': "🎓 *ДОБАВЛЕНИЕ ЭКЗАМЕНА*\n\n✍️ Напишите название предмета или экзамена:\n_(Например: *Высшая Математика*, *Физика*)_",
        'prompt_remind': "⏰ Отправьте напоминание в формате:\n`Читать книгу - 18:30` или `Учеба - 30 минут`",
        'prompt_timezone': "🕒 *ВЫБОР ЧАСОВОГО ПОЯСА*\n\nВыберите ваш город или часовой пояс (чтобы таймер и напоминания работали точно):",
        'prompt_send_location': "📍 *ОТПРАВЬТЕ ГЕОЛОКАЦИЮ ИЛИ ГОРОД*\n\nОтправьте геолокацию (Location) в Telegram или напишите город (Напр: *Москва*, *Ташкент*, *Стамбул*, *Лондон*...):",
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
        'downloading': "Скачивается, пожалуйста подождите...",
        'uploading': "Отправка в Telegram...",
        'error_size': "Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "Произошла ошибка. Попробуйте снова.",
        'schedule_processing': "Создаются обои для экрана блокировки...",
        'schedule_ready_caption': "Расписание занятий (Экран блокировки)",
        'doc_processing': "Файл получен, обрабатывается...",
        'video_error': "Произошла ошибка при загрузке видео.",
        'adhkar_morning_btn': "🌅 Утренние зикры",
        'adhkar_evening_btn': "🌇 Вечерние зикры",
        'adhkar_morning_text': (
            "🌅 *УТРЕННИЕ ЗИКРЫ*\n\n"
            "1. *Аят аль-Курси*\n"
            "2. *Суры Аль-Ихляс, Аль-Фаляк, Ан-Нас* (по 3 раза)\n"
            "3. *«Асбахна ва асбахаль-мульку лиллях, валь-хамду лиллях...»*\n"
            "4. *«Аллахумма бика асбахна, ва бика амсайна, ва бика нахья, ва бика намуту ва илейкан-нушур.»*\n"
            "5. *«Субханаллахи ва бихамдихи»* (100 раз)"
        ),
        'adhkar_evening_text': (
            "🌇 *ВЕЧЕРНИЕ ЗИКРЫ*\n\n"
            "1. *Аят аль-Курси*\n"
            "2. *Суры Аль-Ихляс, Аль-Фаляк, Ан-Нас* (по 3 раза)\n"
            "3. *«Амсайна ва амсаль-мульку лиллях, валь-хамду лиллях...»*\n"
            "4. *«Аллахумма бика амсайна, ва бика асбахна, ва бика нахья, ва бика намуту ва илейкаль-масыр.»*\n"
            "5. *«А'узу би-калиматилляхит-таммати мин шарри ма халяк.»* (3 раза)"
        )
    },
    'en': {
        'welcome': "Hello! Welcome to Nun Bot.\nChoose an option from the menu:",
        'menu_title': "📋 Main Menu:",
        'btn_video': "🎬 Download Video",
        'btn_prayer': "🕌 Prayer Times",
        'btn_pdf_hub': "📄 PDF & Documents",
        'btn_exam': "🎓 Exams & Countdown",
        'btn_schedule_img': "🗓️ Class Schedule (Image)",
        'btn_pomodoro': "⏱️ Pomodoro & Reminders",
        'btn_adhkar': "📿 Adhkar",
        'btn_translit': "🔤 Cyrillic ⇄ Latin",
        'btn_lang': "🌐 Change Language",
        'btn_timezone': "🕒 Timezone",
        'btn_auto_loc': "📍 Auto-Detect (Location / City)",
        'prompt_video': "🔗 Send a link from Instagram, TikTok, Facebook, or X (Twitter):",
        'prompt_prayer': "🕌 *NUN PROJECT // PRAYER TIMES*\n\nType the city name:\n_(e.g. *Kokand*, *Tashkent*, *Istanbul*, *London*...)_",
        'prompt_pdf_hub': "📄 *NUN PROJECT // PDF & DOCUMENTS HUB*\n\nChoose an action:",
        'prompt_schedule_img': "🗓️ *WEEKLY SCHEDULE WALLPAPER*\n\nSend your schedule line by line (e.g. Monday: 09:00 Math...):\nThe bot will generate an aesthetic 1080x1920 lock-screen wallpaper.",
        'prompt_pomodoro': "⏱️ *POMODORO & REMINDERS HUB*",
        'prompt_adhkar': "📿 Choose adhkar category:",
        'prompt_translit': "✍️ Send your text to convert Cyrillic ⇄ Latin:",
        'prompt_convert_to_pdf': "📸 *CONVERT TO PDF ACTIVE*\n\nSend the file you want to convert to PDF:\n_(Image, Word .docx, Excel .xlsx, or TXT)_",
        'prompt_ocr': "🔍 *TEXT EXTRACTION (OCR) ACTIVE*\n\nSend a photo of a whiteboard, book, or notes:\n_(Arabic, Chinese, Russian, Turkish, Uzbek, English and all languages supported)_",
        'prompt_exam_title': "🎓 *ADD EXAM*\n\n✍️ Type the subject or exam title:\n_(e.g. *Calculus Final*, *Physics*)_",
        'prompt_remind': "⏰ Send reminder in format:\n`Read book - 18:30` or `Study - 30 minutes`",
        'prompt_timezone': "🕒 *SELECT TIMEZONE*\n\nChoose your city or timezone (to ensure timers and reminders sync with your local time):",
        'prompt_send_location': "📍 *SHARE LOCATION OR CITY*\n\nPlease share your Location via Telegram or type your city name (e.g. *London*, *Istanbul*, *Tashkent*, *New York*...):",
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
        'downloading': "Downloading media, please wait...",
        'uploading': "Uploading to Telegram...",
        'error_size': "File exceeds Telegram's 50 MB limit.",
        'error_general': "An error occurred. Please try again.",
        'schedule_processing': "Generating lock-screen wallpaper...",
        'schedule_ready_caption': "Class Schedule (Lock Screen)",
        'doc_processing': "File received, processing...",
        'video_error': "An error occurred during video download.",
        'adhkar_morning_btn': "🌅 Morning Adhkar",
        'adhkar_evening_btn': "🌇 Evening Adhkar",
        'adhkar_morning_text': (
            "🌅 *MORNING ADHKAR*\n\n"
            "1. *Ayat al-Kursi*\n"
            "2. *Surahs Al-Ikhlas, Al-Falaq, An-Nas* (3 times each)\n"
            "3. *«Asbahna wa asbahal mulku lillah, walhamdu lillah...»*\n"
            "4. *«Allahumma bika asbahna wa bika amsayna wa bika nahya wa bika namutu wa ilaykan nushur.»*\n"
            "5. *«Subhanallahi wa bihamdihi»* (100 times)"
        ),
        'adhkar_evening_text': (
            "🌇 *EVENING ADHKAR*\n\n"
            "1. *Ayat al-Kursi*\n"
            "2. *Surahs Al-Ikhlas, Al-Falaq, An-Nas* (3 times each)\n"
            "3. *«Amsayna wa amsal mulku lillah, walhamdu lillah...»*\n"
            "4. *«Allahumma bika amsayna wa bika asbahna wa bika nahya wa bika namutu wa ilaykal maseer.»*\n"
            "5. *«A'udhu bi kalimatillahit-tammati min sharri ma khalaq.»* (3 times)"
        )
    }
}

def get_text(user_id, key, context=None) -> str:
    lang = get_user_lang(user_id, context)
    return TEXTS.get(lang, TEXTS['uz']).get(key, TEXTS['uz'].get(key, ''))

def get_reply_menu(user_id, context=None):
    lang = get_user_lang(user_id, context)
    t = TEXTS.get(lang, TEXTS['uz'])
    return ReplyKeyboardMarkup([
        [KeyboardButton(t['btn_video']), KeyboardButton(t['btn_prayer'])],
        [KeyboardButton(t['btn_pdf_hub']), KeyboardButton(t['btn_exam'])],
        [KeyboardButton(t['btn_pomodoro']), KeyboardButton(t['btn_schedule_img'])],
        [KeyboardButton(t['btn_adhkar']), KeyboardButton(t['btn_translit'])],
        [KeyboardButton(t['btn_lang'])]
    ], resize_keyboard=True)

# =====================================================================
# VİDEO İNDİRME MOTORU
# =====================================================================
SUPPORTED_PLATFORMS = [r'(?:instagram\.com)', r'(?:tiktok\.com)', r'(?:facebook\.com|fb\.watch|fb\.gg)', r'(?:twitter\.com|x\.com)']

def is_supported_url(url: str) -> bool:
    return any(re.search(p, url, re.IGNORECASE) for p in SUPPORTED_PLATFORMS)

def download_media_sync(url: str, download_dir: str):
    opts = {
        'outtmpl': os.path.join(download_dir, 'media_%(id)s.%(ext)s'),
        'quiet': True, 'no_warnings': True, 'nocheckcertificate': True,
        'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio/best',
    }
    if shutil.which('ffmpeg'):
        opts['merge_output_format'] = 'mp4'
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Video') if info else 'Video'
        for f in os.listdir(download_dir):
            p = os.path.join(download_dir, f)
            if os.path.isfile(p) and os.path.getsize(p) > 10 * 1024:
                return p, title
    raise RuntimeError("Dosya indirilemedi.")

# =====================================================================
# DİNAMİK BOT KOMUTLARI
# =====================================================================
async def update_user_bot_commands(context: ContextTypes.DEFAULT_TYPE, user_id: int, lang: str):
    commands_map = {
        'uz': [
            BotCommand("start", "Botni ishga tushirish"),
            BotCommand("menu", "Asosiy menyu"),
            BotCommand("namoz", "Namoz vaqtlari"),
            BotCommand("pdf", "PDF & Hujjatlar"),
            BotCommand("imtihon", "Imtihonlar taymeri"),
            BotCommand("cancel", "Bekor qilish"),
        ],
        'tr': [
            BotCommand("start", "Botu başlat"),
            BotCommand("menu", "Ana menü"),
            BotCommand("namaz", "Namaz vakitleri"),
            BotCommand("pdf", "PDF & Belge araçları"),
            BotCommand("sinav", "Sınav & Geri sayım"),
            BotCommand("cancel", "İptal et"),
        ],
        'ru': [
            BotCommand("start", "Запустить бота"),
            BotCommand("menu", "Главное меню"),
            BotCommand("namaz", "Время намаза"),
            BotCommand("pdf", "PDF и Документы"),
            BotCommand("exam", "Таймер экзаменов"),
            BotCommand("cancel", "Отмена"),
        ],
        'en': [
            BotCommand("start", "Start the bot"),
            BotCommand("menu", "Main menu"),
            BotCommand("prayer", "Prayer times"),
            BotCommand("pdf", "PDF & Documents"),
            BotCommand("exam", "Exam countdown"),
            BotCommand("cancel", "Cancel action"),
        ],
    }
    try:
        cmds = commands_map.get(lang, commands_map['uz'])
        await context.bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=user_id))
    except Exception:
        pass

# =====================================================================
# TELEGRAM HANDLERS
# =====================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cleanup_user_temp_files(context, user_id)
    if str(user_id) not in USER_LANGS:
        tele_lang = update.effective_user.language_code or 'uz'
        init_lang = 'tr' if tele_lang.startswith('tr') else ('ru' if tele_lang.startswith('ru') else ('en' if tele_lang.startswith('en') else 'uz'))
        save_user_lang(user_id, init_lang)
    u_lang = get_user_lang(user_id, context)
    await update_user_bot_commands(context, user_id, u_lang)
    await update.message.reply_text(get_text(user_id, 'welcome', context), reply_markup=get_reply_menu(user_id, context))

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cleanup_user_temp_files(context, user_id)
    await update.message.reply_text(get_text(user_id, 'menu_title', context), reply_markup=get_reply_menu(user_id, context))

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cleanup_user_temp_files(context, user_id)
    await update.message.reply_text(get_text(user_id, 'cancel_success', context), reply_markup=get_reply_menu(user_id, context))

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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
    
    card = (
        f"📍 *{t['tz_loc_detected']}*\n\n"
        f"🕒 {t['btn_timezone']}: `UTC{tz_sign}{tz_offset}`\n"
        f"⏰ {t['current_time_lbl']}: `{now_str}`\n\n"
        f"_{t['tz_synced_hint']}_"
    )
    await update.message.reply_text(card, parse_mode="Markdown", reply_markup=get_reply_menu(user_id, context))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    user_lang = get_user_lang(user_id, context)

    # İPTAL VE TEMİZLİK
    if data == "cancel_action":
        cleanup_user_temp_files(context, user_id)
        try: await query.message.delete()
        except Exception: pass
        await context.bot.send_message(chat_id=user_id, text=get_text(user_id, 'cancel_success', context), reply_markup=get_reply_menu(user_id, context))
        return

    # DİL DEĞİŞTİRME
    if data.startswith("lang_"):
        l_code = data.replace("lang_", "", 1).strip()
        save_user_lang(user_id, l_code)
        silent_background_tz_sync(user_id, user_lang_code=l_code)
        if context and context.user_data is not None:
            context.user_data['lang'] = l_code
        try: await query.message.delete()
        except Exception: pass
        await update_user_bot_commands(context, user_id, l_code)
        await context.bot.send_message(
            chat_id=user_id,
            text=f"{TEXTS[l_code]['lang_changed']}\n\n{TEXTS[l_code]['welcome']}",
            reply_markup=get_reply_menu(user_id, context)
        )
        return

    # SAAT DİLİMİ AYARLARI
    if data == "open_tz_selector":
        await query.message.reply_text(
            get_text(user_id, 'prompt_timezone', context),
            parse_mode="Markdown",
            reply_markup=get_timezone_keyboard(user_lang)
        )
        return

    if data == "tz_req_location":
        cleanup_user_temp_files(context, user_id)
        context.user_data['mode'] = 'awaiting_location_or_city'
        await query.message.reply_text(
            get_text(user_id, 'prompt_send_location', context),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]])
        )
        return

    if data.startswith("settz_"):
        parts = data.split("_")
        try:
            val = int(parts)
            save_user_timezone(user_id, val, locked=True)
            try: await query.message.delete()
            except Exception: pass
            
            user_now = get_user_now(user_id, context)
            now_str = user_now.strftime("%H:%M")
            tz_sign = "+" if val >= 0 else ""
            t = TEXTS.get(user_lang, TEXTS['uz'])
            
            msg = (
                f"✅ *{t['tz_changed']}*\n\n"
                f"🕒 {t['btn_timezone']}: `UTC{tz_sign}{val}`\n"
                f"⏰ {t['current_time_lbl']}: `{now_str}`"
            )
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        except Exception:
            pass
        return

    # ZİKİRLERİN İÇERİĞİ
    if data == "adhkar_morning":
        await query.message.reply_text(TEXTS[user_lang]['adhkar_morning_text'], parse_mode="Markdown")
        return

    if data == "adhkar_evening":
        await query.message.reply_text(TEXTS[user_lang]['adhkar_evening_text'], parse_mode="Markdown")
        return

    # PDF HUB SEÇENEKLERİ
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

    # DOĞRUDAN GÖNDERİLEN GÖRSEL İŞLEMLERİ
    if data == "direct_img_pdf":
        img_p = context.user_data.get('direct_file_path')
        if img_p and os.path.exists(img_p):
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_p = os.path.join(tmp_dir, "converted.pdf")
                with Image.open(img_p) as im:
                    if im.mode in ("RGBA", "P"): im = im.convert("RGB")
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
                        with open(t_file, "w", encoding="utf-8") as f_out: f_out.write(txt)
                        with open(t_file, "rb") as f_send:
                            await query.message.reply_document(document=f_send, filename="ocr_text.txt", caption=get_text(user_id, 'ocr_title', context))
                else:
                    await query.message.reply_text(f"{get_text(user_id, 'ocr_title', context)}\n\n`{txt}`", parse_mode="Markdown")
            else:
                await query.message.reply_text(get_text(user_id, 'ocr_fail', context))
        cleanup_user_temp_files(context, user_id)
        return

    # POMODORO VE HATIRLATICI
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
        try: await query.message.delete()
        except Exception: pass
        await query.message.reply_text(f"🗑️ {get_text(user_id, 'remind_deleted', context)}")
        return

    # SINAV VE TAKVİM İŞLEMLERİ
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
        
        if data == "sched_day_prev": cur_dt -= timedelta(days=1)
        elif data == "sched_day_next": cur_dt += timedelta(days=1)
        elif data == "sched_hour_minus": cur_dt -= timedelta(hours=1)
        elif data == "sched_hour_plus": cur_dt += timedelta(hours=1)
        elif data == "sched_min_minus": cur_dt -= timedelta(minutes=15)
        elif data == "sched_min_plus": cur_dt += timedelta(minutes=15)
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
            try: await query.message.delete()
            except Exception: pass
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
# DOSYA & FOTOĞRAF İŞLEYİCİSİ
# =====================================================================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode = context.user_data.get('mode', 'auto')
    doc = update.message.document
    if not doc: return

    fname = doc.file_name or "file"
    ext = os.path.splitext(fname).lower()
    status = await update.message.reply_text(get_text(user_id, 'doc_processing', context))

    try:
        file_obj = await doc.get_file()
        perm_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        perm_tmp.close()
        await file_obj.download_to_drive(perm_tmp.name)

        if mode == 'ocr' and ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            txt = await asyncio.to_thread(extract_text_from_image, perm_tmp.name)
            if txt:
                if len(txt) > 3500:
                    with tempfile.TemporaryDirectory() as t_dir:
                        t_file = os.path.join(t_dir, "ocr_text.txt")
                        with open(t_file, "w", encoding="utf-8") as f_out: f_out.write(txt)
                        with open(t_file, "rb") as f_send:
                            await update.message.reply_document(document=f_send, filename="ocr_text.txt", caption=get_text(user_id, 'ocr_title', context))
                else:
                    await update.message.reply_text(f"{get_text(user_id, 'ocr_title', context)}\n\n`{txt}`", parse_mode="Markdown")
            else:
                await update.message.reply_text(get_text(user_id, 'ocr_fail', context))
            cleanup_user_temp_files(context, user_id)
            try: os.remove(perm_tmp.name)
            except Exception: pass
            return

        if mode == 'convert_to_pdf':
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_pdf = os.path.join(tmp_dir, "converted.pdf")
                ok = False
                if ext in (".docx", ".doc"): ok = await asyncio.to_thread(docx_to_pdf, perm_tmp.name, out_pdf)
                elif ext in (".xlsx", ".xls"): ok = await asyncio.to_thread(xlsx_to_pdf, perm_tmp.name, out_pdf)
                elif ext == ".txt": ok = await asyncio.to_thread(txt_to_pdf, perm_tmp.name, out_pdf)
                elif ext in (".jpg", ".jpeg", ".png"):
                    with Image.open(perm_tmp.name) as im:
                        if im.mode in ("RGBA", "P"): im = im.convert("RGB")
                        im.save(out_pdf, format="PDF")
                    ok = True
                if ok and os.path.exists(out_pdf):
                    with open(out_pdf, "rb") as f:
                        await update.message.reply_document(document=f, filename="converted.pdf", caption=get_text(user_id, 'pdf_ready', context))
                else:
                    await update.message.reply_text(get_text(user_id, 'pdf_fail', context))
            cleanup_user_temp_files(context, user_id)
            try: os.remove(perm_tmp.name)
            except Exception: pass
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
                if ext == ".docx": ok = await asyncio.to_thread(docx_to_pdf, perm_tmp.name, out_pdf)
                elif ext == ".xlsx": ok = await asyncio.to_thread(xlsx_to_pdf, perm_tmp.name, out_pdf)
                elif ext == ".txt": ok = await asyncio.to_thread(txt_to_pdf, perm_tmp.name, out_pdf)
                if ok and os.path.exists(out_pdf):
                    with open(out_pdf, "rb") as f:
                        await update.message.reply_document(document=f, filename="converted.pdf", caption=get_text(user_id, 'pdf_ready', context))
            try: os.remove(perm_tmp.name)
            except Exception: pass
            return

        try: os.remove(perm_tmp.name)
        except Exception: pass
        await update.message.reply_text("⚠️")

    except Exception as e:
        print(f"Hata: {e}")
        await update.message.reply_text(get_text(user_id, 'error_general', context))
    finally:
        try: await status.delete()
        except Exception: pass

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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
                        with open(t_file, "w", encoding="utf-8") as f_out: f_out.write(txt)
                        with open(t_file, "rb") as f_send:
                            await update.message.reply_document(document=f_send, filename="ocr_text.txt", caption=get_text(user_id, 'ocr_title', context))
                else:
                    await update.message.reply_text(f"{get_text(user_id, 'ocr_title', context)}\n\n`{txt}`", parse_mode="Markdown")
            else:
                await update.message.reply_text(get_text(user_id, 'ocr_fail', context))
            cleanup_user_temp_files(context, user_id)
            try: os.remove(perm_tmp.name)
            except Exception: pass
            return

        if mode == 'convert_to_pdf':
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_pdf = os.path.join(tmp_dir, "converted.pdf")
                with Image.open(perm_tmp.name) as im:
                    if im.mode in ("RGBA", "P"): im = im.convert("RGB")
                    im.save(out_pdf, format="PDF")
                with open(out_pdf, "rb") as f:
                    await update.message.reply_document(document=f, filename="converted.pdf", caption=get_text(user_id, 'pdf_ready', context))
            cleanup_user_temp_files(context, user_id)
            try: os.remove(perm_tmp.name)
            except Exception: pass
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
        try: await status.delete()
        except Exception: pass

# =====================================================================
# METİN MESAJ YÖNLENDİRİCİSİ (4 DİL TAM DESTEK)
# =====================================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    raw_text = update.message.text.strip()
    user_lang = get_user_lang(user_id, context)

    # Arka planda sessiz saat dilimi eşitleme
    tele_code = update.effective_user.language_code if update.effective_user else None
    silent_background_tz_sync(user_id, raw_text=raw_text, user_lang_code=tele_code)

    # 1. Menü Butonları Tıklamaları (Tüm diller ve varyasyonlar)
    btn_keys = {
        'btn_video': 'video', 'btn_prayer': 'prayer', 'btn_pdf_hub': 'pdf_hub',
        'btn_exam': 'exam', 'btn_schedule_img': 'schedule_img', 'btn_pomodoro': 'pomodoro',
        'btn_adhkar': 'adhkar', 'btn_translit': 'translit', 'btn_lang': 'lang'
    }
    for b_key, mode_val in btn_keys.items():
        allowed_texts = [TEXTS[l].get(b_key, '') for l in TEXTS]
        if b_key == 'btn_translit':
            allowed_texts.append("🔤 Krill ⇄ Lotin")  # Eski typo uyumluluğu
        if raw_text in allowed_texts:
            cleanup_user_temp_files(context, user_id)
            if mode_val == 'video':
                await update.message.reply_text(get_text(user_id, 'prompt_video', context))
            elif mode_val == 'prayer':
                context.user_data['mode'] = 'prayer'
                await update.message.reply_text(get_text(user_id, 'prompt_prayer', context), parse_mode="Markdown")
            elif mode_val == 'pdf_hub':
                await update.message.reply_text(get_text(user_id, 'prompt_pdf_hub', context), parse_mode="Markdown", reply_markup=get_pdf_hub_keyboard(user_lang))
            elif mode_val == 'exam':
                exams = get_user_exams(user_id)
                cards = [f"📌 *{e.get('title')}* — `{e.get('date')}`" for e in exams] if exams else [get_text(user_id, 'exam_empty', context)]
                hdr = get_text(user_id, 'exam_hub_title', context)
                await update.message.reply_text(f"{hdr}\n\n" + "\n".join(cards), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'exam_btn_add', context), callback_data="exam_add")]]))
            elif mode_val == 'schedule_img':
                context.user_data['mode'] = 'schedule_img_input'
                await update.message.reply_text(get_text(user_id, 'prompt_schedule_img', context), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]]))
            elif mode_val == 'pomodoro':
                await update.message.reply_text(get_text(user_id, 'prompt_pomodoro', context), parse_mode="Markdown", reply_markup=get_pomodoro_keyboard(user_id, user_lang))
            elif mode_val == 'adhkar':
                await update.message.reply_text(get_text(user_id, 'prompt_adhkar', context), reply_markup=get_adhkar_selection_keyboard(user_lang))
            elif mode_val == 'translit':
                context.user_data['mode'] = 'translit'
                await update.message.reply_text(get_text(user_id, 'prompt_translit', context), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'btn_cancel', context), callback_data="cancel_action")]]))
            elif mode_val == 'lang':
                await update.message.reply_text("Tilni tanlang / Dil seçimi / Выберите язык / Select language:", reply_markup=get_language_keyboard())
            return

    # 2. Medya Linki Kontrolü
    if is_supported_url(raw_text):
        url = re.search(r'https?://[^\s]+', raw_text).group(0)
        status = await update.message.reply_text(get_text(user_id, 'downloading', context))
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                f_path, title = await asyncio.to_thread(download_media_sync, url, tmp_dir)
                clean_t = re.sub(r'[\\/*?:"<>|]', '', title)[:60]
                with open(f_path, 'rb') as f:
                    await context.bot.send_video(chat_id=chat_id, video=f, caption=f"🎬 {clean_t}", supports_streaming=True)
            await status.delete()
        except Exception as e:
            print(f"Video hatasi: {e}")
            await status.edit_text(get_text(user_id, 'video_error', context))
        return

    mode = context.user_data.get('mode', 'auto')

    # Konum / Şehir Metni Girişi Bekleniyorsa
    if mode == 'awaiting_location_or_city':
        tz_detected = parse_tz_from_text(raw_text)
        if tz_detected is not None:
            save_user_timezone(user_id, tz_detected, locked=True)
            cleanup_user_temp_files(context, user_id)
            user_now = get_user_now(user_id, context)
            now_str = user_now.strftime("%H:%M")
            t = TEXTS.get(user_lang, TEXTS['uz'])
            tz_sign = "+" if tz_detected >= 0 else ""
            card = (
                f"✅ *{t['tz_loc_detected']}*\n\n"
                f"🕒 {t['btn_timezone']}: `UTC{tz_sign}{tz_detected}`\n"
                f"⏰ {t['current_time_lbl']}: `{now_str}`\n\n"
                f"_{t['tz_synced_hint']}_"
            )
            await update.message.reply_text(card, parse_mode="Markdown", reply_markup=get_reply_menu(user_id, context))
            return

    # 3. KORUMALI SINAV GİRİŞİ (CANLI AYARLAYICI)
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

    # 4. ÇOK DİLLİ HAFTALIK DERS PROGRAMI GÖRSELİ
    if mode == 'schedule_img_input':
        status = await update.message.reply_text(get_text(user_id, 'schedule_processing', context))
        parsed_data = parse_schedule_text(raw_text, user_lang)
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_img = os.path.join(tmp_dir, "timetable_wallpaper.png")
            generate_schedule_wallpaper(parsed_data, out_img, user_lang)
            caption_text = f"📱 {get_text(user_id, 'schedule_ready_caption', context)}"
            with open(out_img, "rb") as f_photo, open(out_img, "rb") as f_doc:
                await update.message.reply_photo(photo=f_photo, caption=caption_text, parse_mode="Markdown")
                await update.message.reply_document(document=f_doc, filename=f"timetable_{user_lang}.png")
        cleanup_user_temp_files(context, user_id)
        try: await status.delete()
        except Exception: pass
        return

    # 5. HATIRLATICI (KULLANICI YEREL ZAMANINA GÖRE)
    if mode == 'remind_input':
        user_now = get_user_now(user_id, context)
        target_dt = None
        rem_text = raw_text
        if "-" in raw_text:
            rem_text, _, time_part = raw_text.partition("-")
            rem_text = rem_text.strip()
            time_part = time_part.strip()
            m_min = re.search(r"(\d+)\s*(?:daqiqa|dakika|min|m|минут)", time_part, re.IGNORECASE)
            if m_min: target_dt = user_now + timedelta(minutes=int(m_min.group(1)))
            else:
                m_t = re.search(r"(\d{1,2})[:.](\d{2})", time_part)
                if m_t:
                    target_dt = user_now.replace(hour=int(m_t.group(1)), minute=int(m_t.group(2)), second=0)
                    if target_dt < user_now: target_dt += timedelta(days=1)

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

    # 6. NAMAZ VAKTİ (VE OTOMATİK SAAT DİLİMİ EŞLEŞTİRME)
    if mode == 'prayer':
        timings, d_name, dt_s, h_s, src = await fetch_prayer_times(raw_text, user_id=user_id)
        if timings:
            await update.message.reply_text(format_prayer_card(d_name, timings, dt_s, h_s, src, user_lang), parse_mode="Markdown")
            cleanup_user_temp_files(context, user_id)
            return
        await update.message.reply_text(get_text(user_id, 'city_not_found', context))
        return

    # 7. ÇEVİRİ
    if mode == 'translit' or len(raw_text.split()) >= 3:
        if is_mostly_cyrillic(raw_text):
            await update.message.reply_text(f"🔤 *Lotin:*\n\n{cyrillic_to_latin(raw_text)}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"🔤 *Кирилл:*\n\n{latin_to_cyrillic(raw_text)}", parse_mode="Markdown")
        return

    # Genel Menü Hatırlatması
    await update.message.reply_text(get_text(user_id, 'menu_title', context), reply_markup=get_reply_menu(user_id, context))

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token: raise ValueError("BOT_TOKEN ortam değişkeni eksik!")
    load_databases()

    # Arka plan keep-alive ve HTTP sağlık sunucusu
    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=run_keep_alive_pinger, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    loop = asyncio.get_event_loop()
    loop.create_task(reminders_worker(app))

    print("Nun Bot 7/24 Kesintisiz Modda Devrede!")
    app.run_polling(drop_pending_updates=True, timeout=30)

if __name__ == "__main__":
    main()
