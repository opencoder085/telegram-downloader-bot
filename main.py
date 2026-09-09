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
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import httpx
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
# RENDER 7/24 HEALTH CHECK & SELF-PING MOTORU
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"NUN BOT 7/24 AKTIF!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

def run_keep_alive_pinger():
    target_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL")
    if not target_url:
        return

    if not target_url.startswith("http"):
        target_url = f"https://{target_url}"

    while True:
        time.sleep(540)  # 9 dakikada bir uyandırma isteği gönderir
        try:
            req = urllib.request.Request(
                target_url,
                headers={'User-Agent': 'NunBot-KeepAlive/1.0'}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                pass
        except Exception:
            pass

# =====================================================================
# SAF PYTHON GÖRSEL ➔ PDF DÖNÜŞTÜRÜCÜSÜ
# =====================================================================
def get_jpeg_size(data: bytes):
    i = 0
    size = len(data)
    while i < size - 1:
        if data[i] == 0xFF:
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            elif marker not in (0xD8, 0xD9, 0x00, 0xFF):
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + length
                continue
        i += 1
    return 800, 1000

def pure_jpeg_to_pdf(jpeg_path: str, pdf_path: str) -> bool:
    try:
        with open(jpeg_path, "rb") as f:
            img_data = f.read()

        width, height = get_jpeg_size(img_data)
        w_pt, h_pt = width * 72 / 96, height * 72 / 96

        objects = []
        objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
        objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
        page = f"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt:.2f} {h_pt:.2f}] /Contents 4 0 R /Resources << /XObject << /Im 5 0 R >> >> >>\nendobj\n".encode()
        objects.append(page)
        content = f"q\n{w_pt:.2f} 0 0 {h_pt:.2f} 0 0 cm\n/Im Do\nQ\n".encode()
        obj4 = f"4 0 obj\n<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream\nendobj\n"
        objects.append(obj4)
        obj5_header = f"5 0 obj\n<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(img_data)} >>\nstream\n".encode()
        obj5 = obj5_header + img_data + b"\nendstream\nendobj\n"
        objects.append(obj5)

        pdf = b"%PDF-1.4\n"
        offsets = [0]
        for obj in objects:
            offsets.append(len(pdf))
            pdf += obj

        xref_pos = len(pdf)
        pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets[1:]:
            pdf += f"{off:010d} 00000 n \n".encode()
        pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()

        with open(pdf_path, "wb") as f:
            f.write(pdf)
        return True
    except Exception as e:
        print(f"pure_jpeg_to_pdf hatası: {e}")
        return False

def convert_image_to_pdf_safe(input_path: str, output_path: str) -> bool:
    try:
        from PIL import Image
        with Image.open(input_path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(output_path, format="PDF", resolution=100.0)
        return True
    except Exception:
        return pure_jpeg_to_pdf(input_path, output_path)

# =====================================================================
# KALICI VERİ YÖNETİM SİSTEMİ (DİL & SINAVLAR)
# =====================================================================
LANG_FILE = "user_langs.json"
EXAMS_FILE = "user_exams.json"
USER_LANGS = {}
USER_EXAMS = {}

def load_databases():
    global USER_LANGS, USER_EXAMS
    if os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, "r", encoding="utf-8") as f:
                USER_LANGS = json.load(f)
        except Exception:
            USER_LANGS = {}

    if os.path.exists(EXAMS_FILE):
        try:
            with open(EXAMS_FILE, "r", encoding="utf-8") as f:
                USER_EXAMS = json.load(f)
        except Exception:
            USER_EXAMS = {}

def save_user_lang(user_id, lang_code: str):
    global USER_LANGS
    uid_str = str(user_id)
    USER_LANGS[uid_str] = lang_code
    try:
        with open(LANG_FILE, "w", encoding="utf-8") as f:
            json.dump(USER_LANGS, f, ensure_ascii=False)
    except Exception:
        pass

def save_user_exams():
    global USER_EXAMS
    try:
        with open(EXAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(USER_EXAMS, f, ensure_ascii=False)
    except Exception:
        pass

def get_user_lang(user_id, context: ContextTypes.DEFAULT_TYPE = None) -> str:
    uid_str = str(user_id)
    if uid_str in USER_LANGS and USER_LANGS[uid_str] in TEXTS:
        return USER_LANGS[uid_str]

    if context and context.user_data and 'lang' in context.user_data:
        l = context.user_data['lang']
        if isinstance(l, str) and l in TEXTS:
            return l

    return 'uz'

def add_user_exam(user_id, title: str, date_str: str) -> str:
    uid_str = str(user_id)
    if uid_str not in USER_EXAMS:
        USER_EXAMS[uid_str] = []

    exam_id = str(int(time.time() * 1000))[-6:]
    USER_EXAMS[uid_str].append({
        'id': exam_id,
        'title': title,
        'date': date_str
    })
    save_user_exams()
    return exam_id

def delete_user_exam(user_id, exam_id: str) -> bool:
    uid_str = str(user_id)
    if uid_str in USER_EXAMS:
        orig_len = len(USER_EXAMS[uid_str])
        USER_EXAMS[uid_str] = [e for e in USER_EXAMS[uid_str] if e.get('id') != exam_id]
        if len(USER_EXAMS[uid_str]) < orig_len:
            save_user_exams()
            return True
    return False

def get_user_exams(user_id) -> list:
    uid_str = str(user_id)
    return USER_EXAMS.get(uid_str, [])

def parse_flexible_date(date_str: str):
    date_str = date_str.strip()
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    return None

def normalize_time_input(t_str: str):
    t_str = t_str.strip().replace(".", ":")
    m = re.match(r"^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$", t_str)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{mn:02d}"
    return None

def format_exam_countdown(exam: dict, lang: str = 'uz') -> str:
    dt = parse_flexible_date(exam.get('date', ''))
    title = exam.get('title', 'Imtihon')
    if not dt:
        return f"📌 *{title}*: `{exam.get('date', '')}`\n"

    now = datetime.now()
    diff = dt - now

    if diff.total_seconds() <= 0:
        if lang == 'tr':
            status = "🏁 _Sınav vakti geldi veya geçti._"
        elif lang == 'ru':
            status = "🏁 _Время экзамена наступило или прошло._"
        elif lang == 'en':
            status = "🏁 _Exam time has arrived or passed._"
        else:
            status = "🏁 _Imtihon vaqti keldi yoki oʻtdi._"
        return f"📌 *{title}*\n📅 `{dt.strftime('%d.%m.%Y %H:%M')}`\n{status}\n"

    days = diff.days
    hours, rem = divmod(diff.seconds, 3600)
    mins, _ = divmod(rem, 60)

    if lang == 'tr':
        cd = f"⏳ *Kalan Süre:* `{days} gün, {hours} saat, {mins} dakika`"
    elif lang == 'ru':
        cd = f"⏳ *Осталось:* `{days} дн., {hours} ч., {mins} мин.`"
    elif lang == 'en':
        cd = f"⏳ *Remaining:* `{days} days, {hours} hrs, {mins} mins`"
    else:
        cd = f"⏳ *Qolgan vaqt:* `{days} kun, {hours} soat, {mins} daqiqa`"

    return f"📌 *{title}*\n📅 `{dt.strftime('%d.%m.%Y %H:%M')}`\n{cd}\n"

# =====================================================================
# KUSURSUZ İNTERAKTİF TELEGRAM TAKVİMİ & SAAT SEÇİCİ
# =====================================================================
def build_inline_calendar(year: int, month: int, lang: str = 'uz') -> InlineKeyboardMarkup:
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
    shortcuts = {
        'uz': ("⚡ Bugun", "⚡ Ertaga", "⚡ 1 hafta", "❌ Bekor qilish"),
        'tr': ("⚡ Bugün", "⚡ Yarın", "⚡ 1 Hafta", "❌ İptal"),
        'ru': ("⚡ Сегодня", "⚡ Завтра", "⚡ 1 неделя", "❌ Отмена"),
        'en': ("⚡ Today", "⚡ Tomorrow", "⚡ 1 Week", "❌ Cancel"),
    }

    m_name = month_names.get(lang, month_names['uz'])[month]
    w_days = week_days.get(lang, week_days['uz'])
    sc = shortcuts.get(lang, shortcuts['uz'])

    rows = []
    # 1. Navigasyon Satırı: ◀️  Ay Yıl  ▶️
    prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    rows.append([
        InlineKeyboardButton("◀️", callback_data=f"cal_nav_{prev_y}_{prev_m}"),
        InlineKeyboardButton(f"{m_name} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton("▶️", callback_data=f"cal_nav_{next_y}_{next_m}")
    ])

    # 2. Haftanın Günleri Başlığı
    rows.append([InlineKeyboardButton(d, callback_data="cal_ignore") for d in w_days])

    # 3. Günler Matrisi
    cal = calendar.monthcalendar(year, month)
    today = datetime.now()

    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
            else:
                d_str = f"{year:04d}-{month:02d}-{day:02d}"
                label = f"•{day}•" if (today.year == year and today.month == month and today.day == day) else str(day)
                row.append(InlineKeyboardButton(label, callback_data=f"cal_date_{d_str}"))
        rows.append(row)

    # 4. Kısayol Butonları (Hatasız Tuple İndekslemesi)
    now = datetime.now()
    t_today = now.strftime("%Y-%m-%d")
    t_tmrw = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    t_week = (now + timedelta(days=7)).strftime("%Y-%m-%d")
    rows.append([
        InlineKeyboardButton(sc[0], callback_data=f"cal_date_{t_today}"),
        InlineKeyboardButton(sc, callback_data=f"cal_date_{t_tmrw}"),
        InlineKeyboardButton(sc, callback_data=f"cal_date_{t_week}"),
    ])
    rows.append([InlineKeyboardButton(sc[3], callback_data="cal_cancel")])
    return InlineKeyboardMarkup(rows)

def build_time_picker(lang: str = 'uz') -> InlineKeyboardMarkup:
    times = [
        ["08:00", "08:30", "09:00", "09:30"],
        ["10:00", "10:30", "11:00", "11:30"],
        ["13:00", "13:30", "14:00", "14:30"],
        ["15:00", "15:30", "16:00", "17:00"],
    ]
    rows = []
    for r in times:
        rows.append([InlineKeyboardButton(t, callback_data=f"time_sel_{t}") for t in r])

    labels = {
        'uz': ("🔙 Sanaga qaytish", "✍️ Boshqa vaqt", "❌ Bekor qilish"),
        'tr': ("🔙 Tarihe Dön", "✍️ Başka Saat", "❌ İptal"),
        'ru': ("🔙 Назад к дате", "✍️ Другое время", "❌ Отмена"),
        'en': ("🔙 Back to Date", "✍️ Custom Time", "❌ Cancel"),
    }
    lbl = labels.get(lang, labels['uz'])
    rows.append([
        InlineKeyboardButton(lbl[0], callback_data="time_back_date"),
        InlineKeyboardButton(lbl, callback_data="time_custom")
    ])
    rows.append([InlineKeyboardButton(lbl[2], callback_data="cal_cancel")])
    return InlineKeyboardMarkup(rows)

async def safe_edit_text_markup(message, text: str, reply_markup=None, parse_mode=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        pass

# =====================================================================
# ÖZBEKÇE KİRİL <-> LATİN ÇEVİRİ MOTORU
# =====================================================================
APOSTROPHES = set(["'", "\u2019", "\u2018", "`", "\u02bb", "\u02bc"])
VOWELS_CYR = set("аоуиэеёюяўАОУИЭЕЁЮЯЎ")

MAP_CYR_TO_LAT = {
    '\u0410': 'A', '\u0430': 'a', '\u0411': 'B', '\u0431': 'b',
    '\u0412': 'V', '\u0432': 'v', '\u0413': 'G', '\u0433': 'g',
    '\u0414': 'D', '\u0434': 'd', '\u0416': 'J', '\u0436': 'j',
    '\u0417': 'Z', '\u0437': 'z', '\u0418': 'I', '\u0438': 'i',
    '\u0419': 'Y', '\u0439': 'y', '\u041a': 'K', '\u043a': 'k',
    '\u049a': 'Q', '\u049b': 'q', '\u041b': 'L', '\u043b': 'l',
    '\u041c': 'M', '\u043c': 'm', '\u041d': 'N', '\u043d': 'n',
    '\u041e': 'O', '\u043e': 'o', '\u041f': 'P', '\u043f': 'p',
    '\u0420': 'R', '\u0440': 'r', '\u0421': 'S', '\u0441': 's',
    '\u0422': 'T', '\u0442': 't', '\u0423': 'U', '\u0443': 'u',
    '\u0424': 'F', '\u0444': 'f', '\u0425': 'X', '\u0445': 'x',
    '\u04b2': 'H', '\u04b3': 'h', '\u042d': 'E', '\u044d': 'e',
}

MAP_LAT_TO_CYR = {
    'A': '\u0410', 'a': '\u0430', 'B': '\u0411', 'b': '\u0431',
    'V': '\u0412', 'v': '\u0432', 'G': '\u0413', 'g': '\u0433',
    'D': '\u0414', 'd': '\u0434', 'J': '\u0416', 'j': '\u0436',
    'Z': '\u0417', 'z': '\u0437', 'I': '\u0418', 'i': '\u0438',
    'Y': '\u0419', 'y': '\u0439', 'K': '\u041a', 'k': '\u043a',
    'Q': '\u049a', 'q': '\u049b', 'L': '\u041b', 'l': '\u043b',
    'M': '\u041c', 'm': '\u043c', 'N': '\u041d', 'n': '\u043d',
    'O': '\u041e', 'o': '\u043e', 'P': '\u041f', 'p': '\u043f',
    'R': '\u0420', 'r': '\u0440', 'S': '\u0421', 's': '\u0441',
    'T': '\u0422', 't': '\u0442', 'U': '\u0423', 'u': '\u0443',
    'F': '\u0424', 'f': '\u0444', 'X': '\u0425', 'x': '\u0445',
    'H': '\u04b2', 'h': '\u04b3',
}

def cyrillic_to_latin(text: str) -> str:
    result = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        prev_char = text[i-1] if i > 0 else " "
        next_char = text[i+1] if i+1 < n else ""
        is_upper = c.isupper()
        next_is_upper = next_char.isupper()

        if c in ('С', 'с') and next_char in ('Ҳ', 'ҳ'):
            res = ("S'H" if next_is_upper else "S'h") if is_upper else "s'h"
            result.append(res)
            i += 2
            continue

        if c in ('Е', 'е'):
            is_word_start = (i == 0 or not prev_char.isalpha())
            after_vowel = (prev_char in VOWELS_CYR or prev_char in 'ъЪьЬ')
            if is_word_start or after_vowel:
                res = ("YE" if next_is_upper else "Ye") if is_upper else "ye"
            else:
                res = "E" if is_upper else "e"
            result.append(res)
            i += 1
            continue

        if c in ('Ё', 'ё'):
            result.append(("YO" if next_is_upper else "Yo") if is_upper else "yo")
            i += 1
            continue
        if c in ('Ю', 'ю'):
            result.append(("YU" if next_is_upper else "Yu") if is_upper else "yu")
            i += 1
            continue
        if c in ('Я', 'я'):
            result.append(("YA" if next_is_upper else "Ya") if is_upper else "ya")
            i += 1
            continue
        if c in ('Ч', 'ч'):
            result.append(("CH" if next_is_upper else "Ch") if is_upper else "ch")
            i += 1
            continue
        if c in ('Ш', 'ш') or c in ('Щ', 'щ'):
            result.append(("SH" if next_is_upper else "Sh") if is_upper else "sh")
            i += 1
            continue
        if c in ('Ц', 'ц'):
            result.append(("TS" if next_is_upper else "Ts") if is_upper else "ts")
            i += 1
            continue
        if c == 'Ў':
            result.append("Oʻ")
            i += 1
            continue
        elif c == 'ў':
            result.append("oʻ")
            i += 1
            continue
        if c == 'Ғ':
            result.append("Gʻ")
            i += 1
            continue
        elif c == 'ғ':
            result.append("gʻ")
            i += 1
            continue
        if c in ('Ъ', 'ъ'):
            result.append("'")
            i += 1
            continue
        if c in ('Ь', 'ь'):
            i += 1
            continue

        result.append(MAP_CYR_TO_LAT.get(c, c))
        i += 1
    return "".join(result)

def latin_to_cyrillic(text: str) -> str:
    result = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        c_next = text[i+1] if i+1 < n else ""
        c_next2 = text[i+2] if i+2 < n else ""
        prev_char = text[i-1] if i > 0 else " "

        if c in ('s', 'S') and c_next in APOSTROPHES and c_next2 in ('h', 'H'):
            res = ("СҲ" if c_next2.isupper() else "Сҳ") if c == 'S' else "сҳ"
            result.append(res)
            i += 3
            continue

        if c in ('o', 'O') and c_next and c_next in APOSTROPHES:
            result.append("Ў" if c == 'O' else "ў")
            i += 2
            continue

        if c in ('g', 'G') and c_next and c_next in APOSTROPHES:
            result.append("Ғ" if c == 'G' else "ғ")
            i += 2
            continue

        if c in ('s', 'S') and c_next in ('h', 'H'):
            result.append("Ш" if c.isupper() else "ш")
            i += 2
            continue

        if c in ('c', 'C') and c_next in ('h', 'H'):
            result.append("Ч" if c.isupper() else "ч")
            i += 2
            continue

        if c in ('t', 'T') and c_next in ('s', 'S'):
            result.append("Ц" if c.isupper() else "ц")
            i += 2
            continue

        if c in ('y', 'Y') and c_next in ('o', 'O'):
            result.append("Ё" if c.isupper() else "ё")
            i += 2
            continue

        if c in ('y', 'Y') and c_next in ('u', 'U'):
            result.append("Ю" if c.isupper() else "ю")
            i += 2
            continue

        if c in ('y', 'Y') and c_next in ('a', 'A'):
            result.append("Я" if c.isupper() else "я")
            i += 2
            continue

        if c in ('y', 'Y') and c_next in ('e', 'E'):
            result.append("Е" if c.isupper() else "е")
            i += 2
            continue

        if c in ('e', 'E'):
            is_word_start = (i == 0 or not prev_char.isalpha())
            after_vowel = (prev_char.lower() in 'aouie')
            if is_word_start or after_vowel:
                result.append("Э" if c == 'E' else "э")
            else:
                result.append("Е" if c == 'E' else "е")
            i += 1
            continue

        if c in APOSTROPHES:
            if prev_char.isalpha():
                result.append("ъ")
            else:
                result.append("'")
            i += 1
            continue

        result.append(MAP_LAT_TO_CYR.get(c, c))
        i += 1
    return "".join(result)

def is_mostly_cyrillic(text: str) -> bool:
    cyr_count = len(re.findall(r'[\u0400-\u04FF]', text))
    lat_count = len(re.findall(r'[a-zA-Z]', text))
    return cyr_count >= lat_count

# =====================================================================
# ÖZBEKİSTAN RESMİ BÖLGE HARİTASI & ALADHAN ENTEGRASYONU
# =====================================================================
UZ_OFFICIAL_REGIONS = {
    "toshkent": "toshkent", "tashkent": "toshkent", "тошкент": "toshkent", "ташкент": "toshkent",
    "qoqon": "qoqon-shahri", "qo'qon": "qoqon-shahri", "qoʻqon": "qoqon-shahri", "kokand": "qoqon-shahri",
    "samarqand": "samarqand-shahri", "samarkand": "samarqand-shahri", "самарқанд": "samarqand-shahri",
    "buxoro": "buxoro-shahri", "bukhara": "buxoro-shahri", "бухоро": "buxoro-shahri",
    "andijon": "andijon-shahri", "andijan": "andijon-shahri", "андижон": "andijon-shahri",
    "namangan": "namangan-shahri", "наманган": "namangan-shahri",
    "fargona": "fargona-shahri", "farg'ona": "fargona-shahri", "fargʻona": "fargona-shahri", "fergana": "fargona-shahri",
    "margilon": "marghilon-shahri", "urganch": "urganch-shahri", "xiva": "xiva-shahri",
    "nukus": "nukus-shahri", "qarshi": "qarshi-shahri", "navoiy": "navoiy-shahri",
    "termiz": "termiz-shahri", "denov": "denov", "guliston": "guliston-shahri",
    "jizzax": "jizzax-shahri", "shahrisabz": "shahrisabz", "angren": "angren", "chirchiq": "chirchiq",
}

GLOBAL_CITY_ALIASES = {
    "istanbul": "Istanbul", "istnbul": "Istanbul", "istambul": "Istanbul", "stambul": "Istanbul",
    "ankara": "Ankara", "izmir": "Izmir", "bursa": "Bursa", "antalya": "Antalya",
    "adana": "Adana", "konya": "Konya", "gaziantep": "Gaziantep", "kayseri": "Kayseri",
    "eskisehir": "Eskisehir", "samsun": "Samsun", "trabzon": "Trabzon",
    "moskva": "Moscow", "moscow": "Moscow", "almaty": "Almaty", "olmaota": "Almaty",
    "astana": "Astana", "bishkek": "Bishkek", "dushanbe": "Dushanbe", "baku": "Baku",
    "makka": "Makkah", "mekke": "Makkah", "mecca": "Makkah",
    "madina": "Medina", "medine": "Medina", "dubai": "Dubai", "dubay": "Dubai",
    "london": "London", "berlin": "Berlin", "paris": "Paris"
}

ALL_KNOWN_CITIES = set(list(UZ_OFFICIAL_REGIONS.keys()) + list(GLOBAL_CITY_ALIASES.keys()))

def is_valid_city_intent(text: str) -> bool:
    clean = re.sub(r"['’`ʻʼ]", "", text.lower().strip())
    clean = clean.replace('i̇', 'i').replace('ı', 'i')
    if clean in ALL_KNOWN_CITIES:
        return True
    if len(clean) >= 4:
        matches = difflib.get_close_matches(clean, list(ALL_KNOWN_CITIES), n=1, cutoff=0.78)
        if matches:
            return True
    return False

def normalize_key(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"['’`ʻʼ]", "", s)
    s = s.replace('i̇', 'i').replace('ı', 'i').replace('ö', 'o').replace('ü', 'u').replace('ş', 's').replace('ç', 'c').replace('ğ', 'g')
    return s

def clean_prayer_query(raw_text: str) -> str:
    text = raw_text.strip()
    text = re.sub(r"^/(?:namaz|namoz|prayer)\s*", "", text, flags=re.IGNORECASE)
    if is_mostly_cyrillic(text):
        text = cyrillic_to_latin(text)

    noise_pattern = r"\b(?:namoz|namaz|vaqtlari|vakitleri|vaqti|vakti|vaqt|vakit|bugun|bugün|today|prayer|times|time|shahar|shahri|viloyat|viloyati|city|город|время|намаз|намаза|ili|ilcesi|ilçesi|uchun|dagi)\b"
    cleaned = re.sub(noise_pattern, " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s'’`ʻʼ-]", "", cleaned).strip()
    return cleaned if cleaned else text

async def fetch_prayer_times(city_input: str):
    city_clean = clean_prayer_query(city_input)
    if not city_clean:
        return None, None, None, None, None

    norm = normalize_key(city_clean)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    async with httpx.AsyncClient(timeout=10.0, verify=False, follow_redirects=True) as client:
        uz_slug = UZ_OFFICIAL_REGIONS.get(norm)
        if not uz_slug:
            matches = difflib.get_close_matches(norm, list(UZ_OFFICIAL_REGIONS.keys()), n=1, cutoff=0.7)
            if matches:
                uz_slug = UZ_OFFICIAL_REGIONS[matches[0]]

        if uz_slug:
            try:
                uz_url = f"https://namoz-vaqti.uz/index.php?format=json&region={uz_slug}"
                resp = await client.get(uz_url, headers=headers)
                if resp.status_code == 200:
                    uz_json = resp.json()
                    times = uz_json.get("today", {}).get("times")
                    if times:
                        timings = {
                            "Fajr": times.get("bomdod", "--:--"),
                            "Sunrise": times.get("quyosh", "--:--"),
                            "Dhuhr": times.get("peshin", "--:--"),
                            "Asr": times.get("asr", "--:--"),
                            "Maghrib": times.get("shom", "--:--"),
                            "Isha": times.get("xufton", "--:--"),
                        }
                        meta = uz_json.get("meta", {})
                        city_disp = meta.get("region", {}).get("name", city_clean.title())
                        date_str = meta.get("date", "")
                        return timings, city_disp, date_str, "", "Oʻzbekiston Din ishlari boʻyicha qoʻmitasi (Rasmiy)"
            except Exception:
                pass

        mapped_city = GLOBAL_CITY_ALIASES.get(norm)
        if not mapped_city:
            matches = difflib.get_close_matches(norm, list(GLOBAL_CITY_ALIASES.keys()), n=1, cutoff=0.7)
            if matches:
                mapped_city = GLOBAL_CITY_ALIASES[matches[0]]

        cand_list = []
        if mapped_city:
            cand_list.append(mapped_city)
        cand_list.append(city_clean)

        for cand in cand_list:
            url = f"https://api.aladhan.com/v1/timingsByAddress?address={urllib.parse.quote(cand)}"
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 200 and "data" in data and "timings" in data["data"]:
                        t = data["data"]["timings"]
                        d = data["data"].get("date", {})
                        g_date = d.get("gregorian", {}).get("date", d.get("readable", ""))
                        hijri = d.get("hijri", {})
                        h_str = f"{hijri.get('day', '')} {hijri.get('month', {}).get('en', '')} {hijri.get('year', '')}".strip()
                        return t, cand, g_date, h_str, "Jonli AlAdhan API orqali olindi"
            except Exception:
                pass

    return None, None, None, None, None

def format_nun_prayer_card(display_name: str, user_input: str, timings: dict, greg_date: str, hijri_str: str, source_note: str, lang: str = 'uz') -> str:
    t_fajr = timings.get("Fajr", "--:--").split()[0]
    t_sunrise = timings.get("Sunrise", "--:--").split()[0]
    t_dhuhr = timings.get("Dhuhr", "--:--").split()[0]
    t_asr = timings.get("Asr", "--:--").split()[0]
    t_maghrib = timings.get("Maghrib", "--:--").split()[0]
    t_isha = timings.get("Isha", "--:--").split()[0]

    clean_disp = re.sub(r"[*_`\[\]]", "", display_name)
    clean_inp = re.sub(r"[*_`\[\]]", "", clean_prayer_query(user_input))
    is_fuzzy = clean_inp.lower() != clean_disp.lower() and bool(clean_inp)

    if lang == 'tr':
        header = "*NUN PROJECT // NAMAZ VAKİTLERİ*"
        lbl_fajr, lbl_sunrise, lbl_dhuhr, lbl_asr, lbl_maghrib, lbl_isha = "İMSAK", "GÜNEŞ", "ÖĞLE", "İKİNDİ", "AKŞAM", "YATSI"
        footer = f"_{source_note}_"
        fuzzy_note = f"\n_🎯 Arama: \"{clean_inp}\" ➔ *{clean_disp}* olarak belirlendi._\n" if is_fuzzy else ""
    elif lang == 'ru':
        header = "*NUN PROJECT // ВРЕМЯ НАМАЗА*"
        lbl_fajr, lbl_sunrise, lbl_dhuhr, lbl_asr, lbl_maghrib, lbl_isha = "ФАДЖР", "ВОСХОД", "ЗУХР", "АСР", "МАГРИБ", "ИША"
        footer = f"_{source_note}_"
        fuzzy_note = f"\n_🎯 Поиск: \"{clean_inp}\" ➔ *{clean_disp}* определено._\n" if is_fuzzy else ""
    elif lang == 'en':
        header = "*NUN PROJECT // PRAYER TIMES*"
        lbl_fajr, lbl_sunrise, lbl_dhuhr, lbl_asr, lbl_maghrib, lbl_isha = "FAJR", "SUNRISE", "DHUHR", "ASR", "MAGHRIB", "ISHA"
        footer = f"_{source_note}_"
        fuzzy_note = f"\n_🎯 Search: \"{clean_inp}\" ➔ Predicted as *{clean_disp}*._\n" if is_fuzzy else ""
    else:  # 'uz'
        header = "*NUN PROJECT // NAMOZ VAQTLARI*"
        lbl_fajr, lbl_sunrise, lbl_dhuhr, lbl_asr, lbl_maghrib, lbl_isha = "BOMDOD", "QUYOSH", "PESHIN", "ASR", "SHOM", "XUFTON"
        footer = f"_{source_note}_"
        fuzzy_note = f"\n_🎯 Qidiruv: \"{clean_inp}\" ➔ *{clean_disp}* deb aniqlandi._\n" if is_fuzzy else ""

    date_line = f"📅 `{greg_date}`" if greg_date else ""
    if hijri_str:
        date_line += f"  •  🌙 `{hijri_str}`" if date_line else f"🌙 `{hijri_str}`"

    card = (
        f"{header}\n"
        f"📍 *[ {clean_disp.upper()} ]*\n"
        f"{date_line}\n"
        f"{fuzzy_note}\n"
        f"┌────────────────────────────┐\n"
        f"  ▫️ *{lbl_fajr}:*{' ' * max(1, 9 - len(lbl_fajr))}`{t_fajr}`\n"
        f"  ▫️ *{lbl_sunrise}:*{' ' * max(1, 9 - len(lbl_sunrise))}`{t_sunrise}`\n"
        f"  ▫️ *{lbl_dhuhr}:*{' ' * max(1, 9 - len(lbl_dhuhr))}`{t_dhuhr}`\n"
        f"  ▫️ *{lbl_asr}:*{' ' * max(1, 9 - len(lbl_asr))}`{t_asr}`\n"
        f"  ▫️ *{lbl_maghrib}:*{' ' * max(1, 9 - len(lbl_maghrib))}`{t_maghrib}`\n"
        f"  ▫️ *{lbl_isha}:*{' ' * max(1, 9 - len(lbl_isha))}`{t_isha}`\n"
        f"└────────────────────────────┘\n"
        f"{footer}"
    )
    return card

# =====================================================================
# SAHİH ZİKİRLER VERİTABANI
# =====================================================================
ADHKAAR_DATA = {
    'morning': [
        {
            'count': "1x",
            'arabic': "أَصْبَحْنَا وَأَصْبَحَ الْمُلْكُ لِلَّهِ، وَالْحَمْدُ لِلَّهِ، لاَ إِلَهَ إِلاَّ اللَّهُ وَحْدَهُ لاَ شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ",
            'uz': "Biz ham, butun mulk ham Allohga tegishli boʻlgan holda tong ottirdik. Hamd Allohgadir. Allohdan oʻzga iloh yoʻq, U yagonadir va sherigi yoʻqdir.",
            'tr': "Biz de mülk de Allah için sabaha erdik. Hamd Allah'adır. Allah'tan başka ilah yoktur, O tektir ve ortağı yoktur.",
            'ru': "Мы дожили до утра, и утро встретила власть, принадлежащая Аллаху. Хвала Аллаху.",
            'en': "We have entered the morning and the kingdom belongs to Allah. All praise is due to Allah."
        }
    ],
    'evening': [
        {
            'count': "1x",
            'arabic': "أَمْسَيْنَا وَأَمْسَى الْمُلْكُ لِلَّهِ، وَالْحَمْدُ لِلَّهِ، لاَ إِلَهَ إِلاَّ اللَّهُ وَحْدَهُ لاَ شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ",
            'uz': "Biz ham, butun mulk ham Allohga tegishli boʻlgan holda kechga yetdik. Hamd Allohgadir.",
            'tr': "Biz de mülk de Allah için akşama erdik. Hamd Allah'adır.",
            'ru': "Мы дожили до вечера, и вечер встретила власть, принадлежащая Аллаху. Хвала Аллаху.",
            'en': "We have reached the evening and at this very time unto Allah belongs all dominion, and all praise is for Allah."
        }
    ]
}

def format_adhkar_card(period: str, lang: str = 'uz') -> str:
    items = ADHKAAR_DATA.get(period, [])
    header = "*NUN PROJECT // TONGGI ZIKRLAR*\n" if period == 'morning' else "*NUN PROJECT // KECHKI ZIKRLAR*\n"
    lines = [header]
    for idx, item in enumerate(items, 1):
        count_str = item.get('count', '1x')
        arabic = item.get('arabic', '')
        meaning = item.get(lang, item.get('uz', ''))
        lines.append(f"▫️ *Zikr* `[{count_str}]`\n📖 {arabic}\n\n💬 _{meaning}_\n────────────────────────────")
    return "\n".join(lines)

# =====================================================================
# METİNLER
# =====================================================================
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Nun Botga xush kelibsiz.\n\nKerakli boʻlimni tanlang:",
        'menu_title': "📋 Asosiy menyu:",
        'btn_video': "🎬 Video yuklash",
        'btn_prayer': "🕌 Namoz vaqtlari",
        'btn_adhkar': "📿 Zikrlar",
        'btn_translit': "🔤 Krill ⇄ Lotin",
        'btn_pdf': "📄 Rasm ➔ PDF",
        'btn_exam': "🎓 Imtihon & Taymer",
        'btn_lang': "🌐 Tilni tanlash",
        'prompt_prayer': "🕌 *NUN PROJECT // NAMOZ VAQTLARI*\n\nNamoz vaqtlarini bilmoqchi boʻlgan shahar nomini yozib yuboring:\n_(Masalan: *Qoʻqon*, *Toshkent*, *Samarqand*, *Istanbul*...)_",
        'prompt_adhkar': "📿 *NUN PROJECT // ZIKRLAR*\n\nQaysi zikrlarni oʻqimoqchisiz? Tanlang:",
        'btn_morning_adhkar': "🌅 Tonggi zikrlar",
        'btn_evening_adhkar': "🌇 Kechki zikrlar",
        'prompt_translit': "✍️ Matningizni yuboring. Bot uni avtomatik ravishda oʻgirib beradi:",
        'prompt_video': "🔗 Instagram, TikTok, Facebook yoki X (Twitter) havolasini yuboring:",
        'prompt_pdf': "📸 PDF formatiga oʻgirmoqchi boʻlgan rasmni yuboring (JPG, PNG):",
        'pdf_processing': "⚙️ Rasm PDF formatiga oʻtkazilmoqda...",
        'pdf_success': "✅ Rasm muvaffaqiyatli PDF hujjatiga aylantirildi!",
        'pdf_error': "❌ Rasmni PDF-ga aylantirishda xatolik yuz berdi.",
        'exam_title': "🎓 *NUN PROJECT // IMTIHONLAR VA DARS JADVALI*",
        'exam_empty': "Sizda hali saqlangan imtihon yoki muhim sana yoʻq.\nQuyidagi tugma orqali yangi imtihon qoʻshishingiz mumkin:",
        'exam_btn_add': "➕ Imtihon qoʻshish",
        'exam_btn_del': "🗑️ Imtihonni oʻchirish",
        'exam_prompt_title': "🎓 *NUN PROJECT // IMTIHON QO'SHISH*\n\n✍️ Imtihon yoki fanning nomini yozib yuboring:\n_(Masalan: *Oliy Matematika*, *Fizika Oraliq*, *Ingliz tili Final*)_",
        'exam_pick_date': "🎓 *NUN PROJECT // SANA TANLASH*\n\n📌 Fan: *{title}*\n\nQuyidagi taqvimdan imtihon kunini tanlang:",
        'exam_pick_time': "🎓 *NUN PROJECT // VAQTNI TANLANG*\n\n📌 Fan: *{title}*\n📅 Sana: `{date}`\n\nImtihon boshlanish vaqtini tanlang:",
        'exam_custom_time_prompt': "✍️ Aniq vaqtni yozib yuboring (Masalan: `10:15` yoki `14:45`):",
        'exam_add_success': "✅ Imtihon muvaffaqiyatli saqlandi!",
        'exam_choose_del': "🗑️ Oʻchirmoqchi boʻlgan imtihonni tanlang:",
        'exam_deleted': "🗑️ Imtihon muvaffaqiyatli oʻchirildi.",
        'downloading': "⏳ Video yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Fayl hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Xatolik yuz berdi. Qaytadan urinib koʻring.",
        'city_not_found': "❌ Shahar aniqlanmadi. Iltimos, shahar nomini toʻgʻri yozing.",
        'lang_changed': "✅ Til muvaffaqiyatli oʻzgartirildi!",
    },
    'tr': {
        'welcome': "Merhaba! Nun Bota hoş geldiniz.\n\nAşağıdaki menüden işlem seçiniz:",
        'menu_title': "📋 Ana Menü:",
        'btn_video': "🎬 Video İndir",
        'btn_prayer': "🕌 Namaz Vakitleri",
        'btn_adhkar': "📿 Zikirler",
        'btn_translit': "🔤 Kiril ⇄ Latin",
        'btn_pdf': "📄 Fotoğraf ➔ PDF",
        'btn_exam': "🎓 Sınav & Geri Sayım",
        'btn_lang': "🌐 Dil Seçimi",
        'prompt_prayer': "🕌 *NUN PROJECT // NAMAZ VAKİTLERİ*\n\nNamaz vakitlerini öğrenmek istediğiniz şehrin adını yazıp gönderin:\n_(Örneğin: *Kokand*, *İstanbul*, *Ankara*, *Taşkent*...)_",
        'prompt_adhkar': "📿 *NUN PROJECT // ZİKİRLER*\n\nHangi zikirleri okumak istersiniz?",
        'btn_morning_adhkar': "🌅 Sabah Zikirleri",
        'btn_evening_adhkar': "🌇 Akşam Zikirleri",
        'prompt_translit': "✍️ Metninizi gönderin. Bot otomatik olarak alfabeyi çevirecektir:",
        'prompt_video': "🔗 Instagram, TikTok, Facebook veya X (Twitter) linki gönderin:",
        'prompt_pdf': "📸 PDF formatına dönüştürmek istediğiniz fotoğrafı gönderin (JPG, PNG):",
        'pdf_processing': "⚙️ Fotoğraf PDF belgesine dönüştürülüyor...",
        'pdf_success': "✅ Fotoğraf başarıyla PDF belgesine dönüştürüldü!",
        'pdf_error': "❌ PDF dönüştürme işlemi sırasında bir hata oluştu.",
        'exam_title': "🎓 *NUN PROJECT // SINAV TAKVİMİ & GERİ SAYIM*",
        'exam_empty': "Henüz kayıtlı bir sınavınız bulunmuyor.\nAşağıdaki butona basarak yeni bir sınav ekleyebilirsiniz:",
        'exam_btn_add': "➕ Sınav Ekle",
        'exam_btn_del': "🗑️ Sınav Sil",
        'exam_prompt_title': "🎓 *NUN PROJECT // SINAV EKLE*\n\n✍️ Sınav veya dersin adını yazıp gönderin:\n_(Örneğin: *Yüksek Matematik*, *Fizik Vize*, *İngilizce*)_",
        'exam_pick_date': "🎓 *NUN PROJECT // TARİH SEÇİMİ*\n\n📌 Ders: *{title}*\n\nAşağıdaki takvimden sınav gününü seçiniz:",
        'exam_pick_time': "🎓 *NUN PROJECT // SAAT SEÇİMİ*\n\n📌 Ders: *{title}*\n📅 Tarih: `{date}`\n\nSınav başlama saatini seçiniz:",
        'exam_custom_time_prompt': "✍️ Tam saati yazıp gönderiniz (Örneğin: `10:15` veya `14:45`):",
        'exam_add_success': "✅ Sınav başarıyla kaydedildi!",
        'exam_choose_del': "🗑️ Silmek istediğiniz sınavı seçiniz:",
        'exam_deleted': "🗑️ Sınav başarıyla silindi.",
        'downloading': "⏳ Medya indiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ Bir hata oluştu. Lütfen tekrar deneyin.",
        'city_not_found': "❌ Şehir bulunamadı. Lütfen geçerli bir şehir adı yazın.",
        'lang_changed': "✅ Dil başarıyla değiştirildi!",
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать в Nun Bot.\n\nВыберите действие в меню:",
        'menu_title': "📋 Главное меню:",
        'btn_video': "🎬 Скачать видео",
        'btn_prayer': "🕌 Время намаза",
        'btn_adhkar': "📿 Зикры",
        'btn_translit': "🔤 Кириллица ⇄ Латиница",
        'btn_pdf': "📄 Фото ➔ PDF",
        'btn_exam': "🎓 Экзамены и Таймер",
        'btn_lang': "🌐 Сменить язык",
        'prompt_prayer': "🕌 *NUN PROJECT // ВРЕМЯ НАМАЗА*\n\nНапишите название города:\n_(Например: *Коканд*, *Ташкент*, *Москва*...)_",
        'prompt_adhkar': "📿 *NUN PROJECT // ЗИКРЫ*\n\nВыберите категорию зикров:",
        'btn_morning_adhkar': "🌅 Утренние зикры",
        'btn_evening_adhkar': "🌇 Вечерние зикры",
        'prompt_translit': "✍️ Отправьте текст для автоматического перевода:",
        'prompt_video': "🔗 Отправьте ссылку из Instagram, TikTok, Facebook или X (Twitter):",
        'prompt_pdf': "📸 Отправьте фото для конвертации в PDF (JPG, PNG):",
        'pdf_processing': "⚙️ Конвертация фото в PDF...",
        'pdf_success': "✅ Фото успешно конвертировано в PDF!",
        'pdf_error': "❌ Ошибка при конвертации фото в PDF.",
        'exam_title': "🎓 *NUN PROJECT // РАСПИСАНИЕ И ТАЙМЕР ЭКЗАМЕНОВ*",
        'exam_empty': "У вас пока нет сохраненных экзаменов.\nВы можете добавить новый ниже:",
        'exam_btn_add': "➕ Добавить экзамен",
        'exam_btn_del': "🗑️ Удалить экзамен",
        'exam_prompt_title': "🎓 *NUN PROJECT // ДОБАВЛЕНИЕ ЭКЗАМЕНА*\n\n✍️ Напишите название предмета или экзамена:\n_(Например: *Высшая Математика*, *Физика*)_",
        'exam_pick_date': "🎓 *NUN PROJECT // ВЫБОР ДАТЫ*\n\n📌 Предмет: *{title}*\n\nВыберите дату экзамена в календаре:",
        'exam_pick_time': "🎓 *NUN PROJECT // ВЫБОР ВРЕМЕНИ*\n\n📌 Предмет: *{title}*\n📅 Дата: `{date}`\n\nВыберите время начала экзамена:",
        'exam_custom_time_prompt': "✍️ Напишите точное время (например: `10:15` или `14:45`):",
        'exam_add_success': "✅ Экзамен успешно сохранен!",
        'exam_choose_del': "🗑️ Выберите экзамен для удаления:",
        'exam_deleted': "🗑️ Экзамен успешно удален.",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Произошла ошибка. Попробуйте снова.",
        'city_not_found': "❌ Город не распознан. Пожалуйста, напишите заново.",
        'lang_changed': "✅ Язык успешно изменен!",
    },
    'en': {
        'welcome': "Hello! Welcome to Nun Bot.\n\nChoose an action from the menu:",
        'menu_title': "📋 Main Menu:",
        'btn_video': "🎬 Download Video",
        'btn_prayer': "🕌 Prayer Times",
        'btn_adhkar': "📿 Adhkar",
        'btn_translit': "🔤 Cyrillic ⇄ Latin",
        'btn_pdf': "📄 Photo ➔ PDF",
        'btn_exam': "🎓 Exams & Countdown",
        'btn_lang': "🌐 Change Language",
        'prompt_prayer': "🕌 *NUN PROJECT // PRAYER TIMES*\n\nType the city name to get prayer times:\n_(e.g. *Kokand*, *Tashkent*, *Istanbul*...)_",
        'prompt_adhkar': "📿 *NUN PROJECT // ADHKAR*\n\nChoose category:",
        'btn_morning_adhkar': "🌅 Morning Adhkar",
        'btn_evening_adhkar': "🌇 Evening Adhkar",
        'prompt_translit': "✍️ Send your text to convert:",
        'prompt_video': "🔗 Send a link from Instagram, TikTok, Facebook, or X (Twitter):",
        'prompt_pdf': "📸 Send the photo you want to convert to PDF (JPG, PNG):",
        'pdf_processing': "⚙️ Converting photo to PDF...",
        'pdf_success': "✅ Photo successfully converted to PDF!",
        'pdf_error': "❌ An error occurred during PDF conversion.",
        'exam_title': "🎓 *NUN PROJECT // EXAM COUNTDOWN & SCHEDULE*",
        'exam_empty': "You don't have any saved exams yet.\nUse the button below to add one:",
        'exam_btn_add': "➕ Add Exam",
        'exam_btn_del': "🗑️ Delete Exam",
        'exam_prompt_title': "🎓 *NUN PROJECT // ADD EXAM*\n\n✍️ Type the subject or exam title:\n_(e.g. *Calculus Final*, *Physics*)_",
        'exam_pick_date': "🎓 *NUN PROJECT // SELECT DATE*\n\n📌 Subject: *{title}*\n\nSelect the exam date from the calendar:",
        'exam_pick_time': "🎓 *NUN PROJECT // SELECT TIME*\n\n📌 Subject: *{title}*\n📅 Date: `{date}`\n\nChoose the exam start time:",
        'exam_custom_time_prompt': "✍️ Type the exact time (e.g. `10:15` or `14:45`):",
        'exam_add_success': "✅ Exam successfully saved!",
        'exam_choose_del': "🗑️ Select an exam to delete:",
        'exam_deleted': "🗑️ Exam successfully deleted.",
        'downloading': "⏳ Downloading media, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ An error occurred. Please try again.",
        'city_not_found': "❌ City could not be detected. Please try again.",
        'lang_changed': "✅ Language updated successfully!",
    }
}

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(1)

def get_text(user_id, key, context: ContextTypes.DEFAULT_TYPE = None) -> str:
    lang = get_user_lang(user_id, context)
    return TEXTS.get(lang, TEXTS['uz']).get(key, '')

def get_reply_menu(user_id, context=None):
    lang = get_user_lang(user_id, context)
    return ReplyKeyboardMarkup([
        [KeyboardButton(TEXTS[lang]['btn_video']), KeyboardButton(TEXTS[lang]['btn_prayer'])],
        [KeyboardButton(TEXTS[lang]['btn_adhkar']), KeyboardButton(TEXTS[lang]['btn_translit'])],
        [KeyboardButton(TEXTS[lang]['btn_pdf']), KeyboardButton(TEXTS[lang]['btn_exam'])],
        [KeyboardButton(TEXTS[lang]['btn_lang'])]
    ], resize_keyboard=True)

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"), InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ])

def get_adhkar_selection_keyboard(user_id, context=None):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text(user_id, 'btn_morning_adhkar', context), callback_data="adhkar_morning"),
            InlineKeyboardButton(get_text(user_id, 'btn_evening_adhkar', context), callback_data="adhkar_evening"),
        ]
    ])

def get_exam_management_keyboard(user_id, context=None):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text(user_id, 'exam_btn_add', context), callback_data="exam_add"),
            InlineKeyboardButton(get_text(user_id, 'exam_btn_del', context), callback_data="exam_del_menu"),
        ]
    ])

# =====================================================================
# VİDEO İNDİRME MOTORU
# =====================================================================
SUPPORTED_PLATFORMS = [
    r'(?:instagram\.com)',
    r'(?:tiktok\.com)',
    r'(?:facebook\.com|fb\.watch|fb\.gg)',
    r'(?:twitter\.com|x\.com)',
]

def is_supported_url(url: str) -> bool:
    return any(re.search(p, url, re.IGNORECASE) for p in SUPPORTED_PLATFORMS)

def get_downloaded_media_file(directory: str):
    valid_files = []
    for f in os.listdir(directory):
        full_path = os.path.join(directory, f)
        if os.path.isfile(full_path):
            if not f.endswith(('.part', '.ytdl', '.temp', '.aria2', '.json', '.jpg', '.jpeg', '.png', '.webp', '.vtt', '.srt')):
                if os.path.getsize(full_path) > 10 * 1024:
                    valid_files.append(full_path)
    if not valid_files:
        return None
    valid_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
    return valid_files[0]

async def safe_edit_text(msg, text):
    try:
        await msg.edit_text(text)
    except Exception:
        pass

def download_media_sync(url: str, download_dir: str):
    has_ffmpeg = shutil.which('ffmpeg') is not None
    opts = {
        'outtmpl': os.path.join(download_dir, 'media_%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 30,
        'retries': 5,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio/best',
    }
    if has_ffmpeg:
        opts['merge_output_format'] = 'mp4'

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Video') if info else 'Video'
        final_file = get_downloaded_media_file(download_dir)
        if not final_file:
            raise RuntimeError("Dosya indirilemedi.")
        return final_file, title

async def process_download(status_msg, user_id, chat_id, url, context):
    async with DOWNLOAD_SEMAPHORE:
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                file_path, title = await asyncio.to_thread(download_media_sync, url, tmp_dir)

                size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if size_mb > 49.5:
                    await safe_edit_text(status_msg, get_text(user_id, 'error_size', context))
                    return

                await safe_edit_text(status_msg, get_text(user_id, 'uploading', context))
                clean_title = re.sub(r'[\\/*?:"<>|]', '', title)[:60]

                try:
                    with open(file_path, 'rb') as f:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=f,
                            caption=f"🎬 {clean_title}",
                            supports_streaming=True,
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=60,
                        )
                except Exception:
                    with open(file_path, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            caption=f"🎬 {clean_title}",
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=60,
                        )

                try:
                    await status_msg.delete()
                except Exception:
                    pass

        except Exception as e:
            print(f"İndirme Hatası [{url}]: {e}")
            await safe_edit_text(status_msg, get_text(user_id, 'error_general', context))

# =====================================================================
# TELEGRAM ETKİLEŞİM İŞLEYİCİLERİ
# =====================================================================
async def update_user_bot_commands(context: ContextTypes.DEFAULT_TYPE, user_id: int, lang: str):
    commands_map = {
        'uz': [
            BotCommand("start", "Botni ishga tushirish"),
            BotCommand("menu", "Asosiy menyu"),
            BotCommand("namoz", "Namoz vaqtlari"),
            BotCommand("zikr", "Tonggi va kechki zikrlar"),
            BotCommand("pdf", "Rasm ➔ PDF aylantirish"),
            BotCommand("imtihon", "Imtihonlar taymeri"),
        ],
        'ru': [
            BotCommand("start", "Запустить бота"),
            BotCommand("menu", "Главное меню"),
            BotCommand("namaz", "Время намаза"),
            BotCommand("zikr", "Утренние и вечерние зикры"),
            BotCommand("pdf", "Конвертировать фото в PDF"),
            BotCommand("exam", "Таймер экзаменов"),
        ],
        'en': [
            BotCommand("start", "Start the bot"),
            BotCommand("menu", "Main menu"),
            BotCommand("prayer", "Prayer times"),
            BotCommand("zikr", "Morning and evening adhkar"),
            BotCommand("pdf", "Convert photo to PDF"),
            BotCommand("exam", "Exam countdown timer"),
        ],
        'tr': [
            BotCommand("start", "Botu başlat"),
            BotCommand("menu", "Ana menü"),
            BotCommand("namaz", "Namaz vakitleri"),
            BotCommand("zikr", "Sabah ve akşam zikirleri"),
            BotCommand("pdf", "Fotoğraf ➔ PDF dönüştürücü"),
            BotCommand("sinav", "Sınav takvimi & Geri sayım"),
        ],
    }
    try:
        cmds = commands_map.get(lang, commands_map['uz'])
        await context.bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=user_id))
    except Exception:
        pass

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if str(user_id) not in USER_LANGS:
        tele_lang = update.effective_user.language_code or 'uz'
        if tele_lang.startswith('tr'):
            init_lang = 'tr'
        elif tele_lang.startswith('ru'):
            init_lang = 'ru'
        elif tele_lang.startswith('en'):
            init_lang = 'en'
        else:
            init_lang = 'uz'
        save_user_lang(user_id, init_lang)
        if context and context.user_data is not None:
            context.user_data['lang'] = init_lang

    current_lang = get_user_lang(user_id, context)
    await update_user_bot_commands(context, user_id, current_lang)
    await update.message.reply_text(
        "Tilni tanlang / Lütfen dil seçin / Выберите язык / Select language:",
        reply_markup=get_language_keyboard()
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data:
        context.user_data['mode'] = 'auto'
        context.user_data.pop('exam_draft_title', None)
        context.user_data.pop('exam_draft_date', None)

    await update.message.reply_text(
        get_text(user_id, 'menu_title', context),
        reply_markup=get_reply_menu(user_id, context)
    )

async def prayer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id, context)

    if context.args:
        city_query = " ".join(context.args)
        timings, resolved_name, g_date, h_str, source_note = await fetch_prayer_times(city_query)
        if timings:
            disp_name = resolved_name.title() if resolved_name else city_query.title()
            card = format_nun_prayer_card(disp_name, city_query, timings, g_date, h_str, source_note, user_lang)
            try:
                await update.message.reply_text(card, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(card)
            context.user_data['mode'] = 'prayer'
            return
        else:
            await update.message.reply_text(get_text(user_id, 'city_not_found', context), parse_mode="Markdown")
            return

    context.user_data['mode'] = 'prayer'
    await update.message.reply_text(
        get_text(user_id, 'prompt_prayer', context),
        parse_mode="Markdown"
    )

async def adhkar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        get_text(user_id, 'prompt_adhkar', context),
        reply_markup=get_adhkar_selection_keyboard(user_id, context),
        parse_mode="Markdown"
    )

async def pdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['mode'] = 'pdf'
    await update.message.reply_text(get_text(user_id, 'prompt_pdf', context))

async def exam_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id, context)
    exams = get_user_exams(user_id)

    header = get_text(user_id, 'exam_title', context)
    if not exams:
        body = get_text(user_id, 'exam_empty', context)
        text = f"{header}\n\n{body}"
    else:
        cards = [format_exam_countdown(e, user_lang) for e in exams]
        text = f"{header}\n\n" + "\n".join(cards)

    msg = update.message if update.message else update.callback_query.message
    await msg.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_exam_management_keyboard(user_id, context)
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    user_lang = get_user_lang(user_id, context)

    # 1. DİL DEĞİŞİMİ
    if data.startswith("lang_"):
        selected_lang = data.split("_")
        if selected_lang not in TEXTS:
            selected_lang = 'uz'

        save_user_lang(user_id, selected_lang)
        if context and context.user_data is not None:
            context.user_data['lang'] = selected_lang

        try:
            await query.message.delete()
        except Exception:
            pass

        await update_user_bot_commands(context, user_id, selected_lang)
        new_menu = get_reply_menu(user_id, context)
        welcome_text = TEXTS[selected_lang]['welcome']
        changed_text = TEXTS[selected_lang]['lang_changed']

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ {changed_text}\n\n{welcome_text}",
            reply_markup=new_menu
        )
        return

    # 2. ZİKİR SEÇİMİ
    if data in ("adhkar_morning", "adhkar_evening"):
        period = "morning" if data == "adhkar_morning" else "evening"
        card = format_adhkar_card(period, user_lang)
        other_period = "evening" if period == "morning" else "morning"
        other_btn_text = get_text(user_id, f'btn_{other_period}_adhkar', context)
        nav_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"➡️ {other_btn_text}", callback_data=f"adhkar_{other_period}")]
        ])
        try:
            await query.message.reply_text(card, parse_mode="Markdown", reply_markup=nav_keyboard)
        except Exception:
            await query.message.reply_text(card, reply_markup=nav_keyboard)
        return

    # 3. YENİ SINAV EKLEME: SADECE ADINI İSTE
    if data == "exam_add":
        context.user_data['mode'] = 'exam_title_input'
        await query.message.reply_text(
            get_text(user_id, 'exam_prompt_title', context),
            parse_mode="Markdown"
        )
        return

    # 4. İNTERAKTİF TAKVİM: AY DEĞİŞTİRME (◀️  ▶️)
    if data.startswith("cal_nav_"):
        parts = data.split("_")
        y, m = int(parts), int(parts)
        title = context.user_data.get('exam_draft_title', 'Imtihon')
        kb = build_inline_calendar(y, m, user_lang)
        prompt = get_text(user_id, 'exam_pick_date', context).format(title=title)
        await safe_edit_text_markup(query.message, prompt, kb, parse_mode="Markdown")
        return

    # 5. İNTERAKTİF TAKVİM: GÜN SEÇİLDİ ➔ SAAT SEÇİCİYİ AÇ
    if data.startswith("cal_date_"):
        date_str = data.replace("cal_date_", "").strip()
        context.user_data['exam_draft_date'] = date_str
        context.user_data['mode'] = 'exam_time_select'
        title = context.user_data.get('exam_draft_title', 'Imtihon')
        kb = build_time_picker(user_lang)
        prompt = get_text(user_id, 'exam_pick_time', context).format(title=title, date=date_str)
        await safe_edit_text_markup(query.message, prompt, kb, parse_mode="Markdown")
        return

    # 6. SAAT SEÇİCİDEN TARİHE GERİ DÖN
    if data == "time_back_date":
        now = datetime.now()
        title = context.user_data.get('exam_draft_title', 'Imtihon')
        kb = build_inline_calendar(now.year, now.month, user_lang)
        prompt = get_text(user_id, 'exam_pick_date', context).format(title=title)
        await safe_edit_text_markup(query.message, prompt, kb, parse_mode="Markdown")
        return

    # 7. ÖZEL SAAT YAZMA BUTONU
    if data == "time_custom":
        context.user_data['mode'] = 'exam_custom_time'
        await query.message.reply_text(
            get_text(user_id, 'exam_custom_time_prompt', context),
            parse_mode="Markdown"
        )
        return

    # 8. SAAT BUTONUNA TIKLANDI ➔ SINAVI KAYDET
    if data.startswith("time_sel_"):
        chosen_time = data.replace("time_sel_", "").strip()
        date_str = context.user_data.get('exam_draft_date', datetime.now().strftime("%Y-%m-%d"))
        title = context.user_data.get('exam_draft_title', 'Imtihon')
        full_datetime_str = f"{date_str} {chosen_time}"

        exam_id = add_user_exam(user_id, title, full_datetime_str)
        context.user_data['mode'] = 'auto'
        context.user_data.pop('exam_draft_title', None)
        context.user_data.pop('exam_draft_date', None)

        success_text = get_text(user_id, 'exam_add_success', context)
        exam_obj = {'id': exam_id, 'title': title, 'date': full_datetime_str}
        card = format_exam_countdown(exam_obj, user_lang)

        list_lbl = "📋 Ro'yxat" if user_lang == 'uz' else ("📋 Liste" if user_lang == 'tr' else "📋 List")
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(get_text(user_id, 'exam_btn_add', context), callback_data="exam_add"),
                InlineKeyboardButton(list_lbl, callback_data="exam_list")
            ]
        ])
        await query.message.reply_text(
            f"{success_text}\n\n{card}",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    # 9. TAKVİM İPTAL ETME
    if data == "cal_cancel":
        context.user_data['mode'] = 'auto'
        context.user_data.pop('exam_draft_title', None)
        context.user_data.pop('exam_draft_date', None)
        try:
            await query.message.delete()
        except Exception:
            pass
        await exam_command(update, context)
        return

    # 10. SINAV LİSTESİNE GERİ DÖNÜŞ
    if data == "exam_list":
        await exam_command(update, context)
        return

    # 11. SINAV SİLME MENÜSÜ
    if data == "exam_del_menu":
        exams = get_user_exams(user_id)
        if not exams:
            await query.message.reply_text(get_text(user_id, 'exam_empty', context))
            return

        buttons = []
        for e in exams:
            buttons.append([InlineKeyboardButton(f"❌ {e.get('title', 'Sınav')}", callback_data=f"exam_del_{e.get('id')}")])

        await query.message.reply_text(
            get_text(user_id, 'exam_choose_del', context),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # 12. SINAV SİL
    if data.startswith("exam_del_"):
        target_id = data.replace("exam_del_", "").strip()
        delete_user_exam(user_id, target_id)
        try:
            await query.message.delete()
        except Exception:
            pass

        await context.bot.send_message(
            chat_id=user_id,
            text=get_text(user_id, 'exam_deleted', context)
        )
        await exam_command(update, context)
        return

# =====================================================================
# FOTOĞRAF ➔ PDF DÖNÜŞTÜRÜCÜ MOTORU
# =====================================================================
async def handle_photo_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if update.message.photo:
        file_obj = await update.message.photo[-1].get_file()
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("image/"):
        file_obj = await update.message.document.get_file()
    else:
        return

    status_msg = await update.message.reply_text(get_text(user_id, 'pdf_processing', context))

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = os.path.join(tmp_dir, "input_photo.jpg")
            output_pdf = os.path.join(tmp_dir, "nun_belge.pdf")

            await file_obj.download_to_drive(input_path)

            success = convert_image_to_pdf_safe(input_path, output_pdf)
            if not success or not os.path.exists(output_pdf):
                raise RuntimeError("PDF oluşturulamadı.")

            with open(output_pdf, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename="nun_belge.pdf",
                    caption=get_text(user_id, 'pdf_success', context)
                )

            try:
                await status_msg.delete()
            except Exception:
                pass

    except Exception as e:
        print(f"PDF Dönüştürme Hatası: {e}")
        await safe_edit_text(status_msg, get_text(user_id, 'pdf_error', context))

# =====================================================================
# KESİN MESAJ YÖNLENDİRİCİSİ (STATE ROUTER)
# =====================================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    raw_text = update.message.text.strip()
    user_lang = get_user_lang(user_id, context)

    # 1. Menü Butonları Tıklamaları (Her an durumları sıfırlar ve menüyü açar)
    btn_vid = [TEXTS[l]['btn_video'] for l in TEXTS]
    btn_pry = [TEXTS[l]['btn_prayer'] for l in TEXTS]
    btn_adh = [TEXTS[l]['btn_adhkar'] for l in TEXTS]
    btn_trn = [TEXTS[l]['btn_translit'] for l in TEXTS]
    btn_pdf = [TEXTS[l]['btn_pdf'] for l in TEXTS]
    btn_exm = [TEXTS[l]['btn_exam'] for l in TEXTS]
    btn_lng = [TEXTS[l]['btn_lang'] for l in TEXTS]

    if raw_text in btn_vid:
        context.user_data['mode'] = 'video'
        context.user_data.pop('exam_draft_title', None)
        await update.message.reply_text(get_text(user_id, 'prompt_video', context))
        return

    if raw_text in btn_pry:
        context.user_data['mode'] = 'prayer'
        context.user_data.pop('exam_draft_title', None)
        await update.message.reply_text(
            get_text(user_id, 'prompt_prayer', context),
            parse_mode="Markdown"
        )
        return

    if raw_text in btn_adh:
        context.user_data['mode'] = 'adhkar'
        context.user_data.pop('exam_draft_title', None)
        await update.message.reply_text(
            get_text(user_id, 'prompt_adhkar', context),
            reply_markup=get_adhkar_selection_keyboard(user_id, context),
            parse_mode="Markdown"
        )
        return

    if raw_text in btn_trn:
        context.user_data['mode'] = 'translit'
        context.user_data.pop('exam_draft_title', None)
        await update.message.reply_text(get_text(user_id, 'prompt_translit', context))
        return

    if raw_text in btn_pdf:
        context.user_data['mode'] = 'pdf'
        context.user_data.pop('exam_draft_title', None)
        await update.message.reply_text(get_text(user_id, 'prompt_pdf', context))
        return

    if raw_text in btn_exm:
        context.user_data['mode'] = 'auto'
        context.user_data.pop('exam_draft_title', None)
        await exam_command(update, context)
        return

    if raw_text in btn_lng:
        await update.message.reply_text(
            "Tilni tanlang / Lütfen dil seçin / Выберите язык / Select language:",
            reply_markup=get_language_keyboard()
        )
        return

    # 2. Medya İndirme Linki (Tüm modlardan önceliklidir)
    url_match = re.search(r'https?://[^\s]+', raw_text)
    if url_match:
        url = url_match.group(0)
        if is_supported_url(url):
            status_msg = await update.message.reply_text(get_text(user_id, 'downloading', context))
            asyncio.create_task(process_download(status_msg, user_id, chat_id, url, context))
            return

    current_mode = context.user_data.get('mode', 'auto')
    lower_text = raw_text.lower().strip()

    # 3. KORUMALI SINAV AKIŞI (BAŞKA MODLARA ASLA SIZAMAZ)
    if current_mode == 'exam_title_input':
        # Kullanıcı tek satırda mı yazdı? (Örn: Matematika - 15.11.2026 10:00)
        if "-" in raw_text and any(c.isdigit() for c in raw_text):
            parts = raw_text.split("-", 1)
            title = parts[0].strip()
            date_part = parts.strip()
            parsed_dt = parse_flexible_date(date_part)
            if parsed_dt:
                exam_id = add_user_exam(user_id, title, parsed_dt.strftime("%Y-%m-%d %H:%M"))
                context.user_data['mode'] = 'auto'
                await update.message.reply_text(get_text(user_id, 'exam_add_success', context))
                await exam_command(update, context)
                return

        # Sadece ders adını yazdıysa (Örn: Matematik veya Yaramaz) ➔ Takvimi aç
        context.user_data['exam_draft_title'] = raw_text.strip()
        context.user_data['mode'] = 'exam_date_select'
        now = datetime.now()
        kb = build_inline_calendar(now.year, now.month, user_lang)
        prompt = get_text(user_id, 'exam_pick_date', context).format(title=raw_text.strip())
        await update.message.reply_text(prompt, parse_mode="Markdown", reply_markup=kb)
        return

    if current_mode == 'exam_date_select':
        # Kullanıcı takvim butonuna basmak yerine yazı yazarsa takvimi tekrar göster
        now = datetime.now()
        title = context.user_data.get('exam_draft_title', 'Imtihon')
        kb = build_inline_calendar(now.year, now.month, user_lang)
        prompt = get_text(user_id, 'exam_pick_date', context).format(title=title)
        await update.message.reply_text(
            f"⚠️ Iltimos, quyidagi taqvimdan sanani tanlang:\n\n{prompt}",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    if current_mode == 'exam_time_select':
        # Kullanıcı saat butonuna basmak yerine doğrudan '10:30' yazarsa kaydet
        norm_time = normalize_time_input(raw_text)
        if norm_time:
            date_str = context.user_data.get('exam_draft_date', datetime.now().strftime("%Y-%m-%d"))
            title = context.user_data.get('exam_draft_title', 'Imtihon')
            full_datetime_str = f"{date_str} {norm_time}"

            exam_id = add_user_exam(user_id, title, full_datetime_str)
            context.user_data['mode'] = 'auto'
            context.user_data.pop('exam_draft_title', None)
            context.user_data.pop('exam_draft_date', None)

            success_text = get_text(user_id, 'exam_add_success', context)
            exam_obj = {'id': exam_id, 'title': title, 'date': full_datetime_str}
            card = format_exam_countdown(exam_obj, user_lang)
            await update.message.reply_text(f"{success_text}\n\n{card}", parse_mode="Markdown")
            await exam_command(update, context)
            return
        else:
            title = context.user_data.get('exam_draft_title', 'Imtihon')
            date_str = context.user_data.get('exam_draft_date', '')
            kb = build_time_picker(user_lang)
            prompt = get_text(user_id, 'exam_pick_time', context).format(title=title, date=date_str)
            await update.message.reply_text(prompt, parse_mode="Markdown", reply_markup=kb)
            return

    if current_mode == 'exam_custom_time':
        norm_time = normalize_time_input(raw_text)
        if norm_time:
            date_str = context.user_data.get('exam_draft_date', datetime.now().strftime("%Y-%m-%d"))
            title = context.user_data.get('exam_draft_title', 'Imtihon')
            full_datetime_str = f"{date_str} {norm_time}"

            exam_id = add_user_exam(user_id, title, full_datetime_str)
            context.user_data['mode'] = 'auto'
            context.user_data.pop('exam_draft_title', None)
            context.user_data.pop('exam_draft_date', None)

            success_text = get_text(user_id, 'exam_add_success', context)
            exam_obj = {'id': exam_id, 'title': title, 'date': full_datetime_str}
            card = format_exam_countdown(exam_obj, user_lang)
            await update.message.reply_text(f"{success_text}\n\n{card}", parse_mode="Markdown")
            await exam_command(update, context)
            return
        else:
            await update.message.reply_text(get_text(user_id, 'exam_custom_time_prompt', context), parse_mode="Markdown")
            return

    # 4. KULLANICI AÇIKÇA ZİKİR İSTEDİĞİNDE
    if bool(re.search(r'\b(zikr|zikirlar|zikirler|adhkar|azkar|зикры|зикр)\b', lower_text)):
        await update.message.reply_text(
            get_text(user_id, 'prompt_adhkar', context),
            reply_markup=get_adhkar_selection_keyboard(user_id, context),
            parse_mode="Markdown"
        )
        return

    # 5. KULLANICI AÇIKÇA NAMAZ VAKTİ İSTEDİĞİNDE
    is_prayer_intent = bool(re.search(r'\b(namoz|namaz|prayer|vaqtlari|vakitleri|vaqti|vakti)\b', lower_text))
    if current_mode == 'prayer' or is_prayer_intent:
        timings, resolved_name, g_date, h_str, source_note = await fetch_prayer_times(raw_text)
        if timings:
            disp_name = resolved_name.title() if resolved_name else clean_prayer_query(raw_text).title()
            card = format_nun_prayer_card(disp_name, raw_text, timings, g_date, h_str, source_note, user_lang)
            try:
                await update.message.reply_text(card, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(card)
            context.user_data['mode'] = 'auto'
            return
        else:
            await update.message.reply_text(get_text(user_id, 'city_not_found', context), parse_mode="Markdown")
            return

    # 6. SADECE GERÇEK BİR ŞEHİR ADI YAZILDIYSA NAMAZ VAKTİ VER
    if is_valid_city_intent(raw_text):
        timings, resolved_name, g_date, h_str, source_note = await fetch_prayer_times(raw_text)
        if timings:
            disp_name = resolved_name.title() if resolved_name else clean_prayer_query(raw_text).title()
            card = format_nun_prayer_card(disp_name, raw_text, timings, g_date, h_str, source_note, user_lang)
            try:
                await update.message.reply_text(card, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(card)
            return

    # 7. METİN ÇEVİRİSİ (Sadece çeviri modundaysa veya uzun cümlelerde)
    if current_mode == 'translit' or len(raw_text.split()) >= 3:
        if is_mostly_cyrillic(raw_text):
            converted = cyrillic_to_latin(raw_text)
            await update.message.reply_text(f"🔤 *Lotin:*\n\n{converted}", parse_mode="Markdown")
        else:
            converted = latin_to_cyrillic(raw_text)
            await update.message.reply_text(f"🔤 *Кирилл:*\n\n{converted}", parse_mode="Markdown")
        return

    # 8. Tanınmayan kısa rastgele yazılarda menüyü hatırlat
    await update.message.reply_text(
        get_text(user_id, 'menu_title', context),
        reply_markup=get_reply_menu(user_id, context)
    )

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN ortam değişkeni eksik!")

    load_databases()

    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=run_keep_alive_pinger, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("namoz", prayer_command))
    app.add_handler(CommandHandler("namaz", prayer_command))
    app.add_handler(CommandHandler("zikr", adhkar_command))
    app.add_handler(CommandHandler("pdf", pdf_command))
    app.add_handler(CommandHandler("sinav", exam_command))
    app.add_handler(CommandHandler("imtihon", exam_command))
    app.add_handler(CommandHandler("exam", exam_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo_to_pdf))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Nun Bot aktif; Kusursuz İnteraktif Takvim ve Korumalı Durum Makinesi Devrede!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
