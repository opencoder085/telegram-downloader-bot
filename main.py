import os
import re
import json
import ssl
import shutil
import asyncio
import tempfile
import threading
import urllib.parse
import urllib.request
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

# --- RENDER 7/24 SAĞLIK KONTROLÜ (PORT BINDING) ---
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
# ŞEHİR ADI NORMALİZASYONU VE VARYANT HARİTASI
# =====================================================================
CITY_ALIASES = {
    # Özbekistan ve Fergana Vadisi
    "kokand": "Kokand", "qoqon": "Kokand", "qo'qon": "Kokand", "qoʻqon": "Kokand", "коканд": "Kokand",
    "toshkent": "Tashkent", "toskent": "Tashkent", "toshken": "Tashkent", "тошкент": "Tashkent", "ташкент": "Tashkent", "tashkent": "Tashkent",
    "samarqand": "Samarkand", "samarkant": "Samarkand", "самарқанд": "Samarkand", "самарканд": "Samarkand", "samarkand": "Samarkand",
    "buxoro": "Bukhara", "buhara": "Bukhara", "бухоро": "Bukhara", "bukhara": "Bukhara",
    "andijon": "Andijan", "andjan": "Andijan", "андижон": "Andijan", "andijan": "Andijan",
    "fargona": "Fergana", "farg'ona": "Fergana", "fargʻona": "Fergana", "фарғона": "Fergana", "фергана": "Fergana", "fergana": "Fergana",
    "margilon": "Margilan", "marg'ilon": "Margilan", "марғилон": "Margilan", "margilan": "Margilan",
    "urganch": "Urgench", "урганч": "Urgench", "urgench": "Urgench",
    "xiva": "Khiva", "khiva": "Khiva", "хива": "Khiva",
    "nukus": "Nukus", "нукус": "Nukus",
    "jizzax": "Jizzakh", "жиззах": "Jizzakh", "jizzakh": "Jizzakh",
    "navoiy": "Navoiy", "навоий": "Navoiy", "navoi": "Navoiy",
    "termiz": "Termez", "термиз": "Termez", "termez": "Termez",
    "denov": "Denau", "денов": "Denau", "denau": "Denau",
    "qarshi": "Qarshi", "қарши": "Qarshi",
    "guliston": "Gulistan", "гулистон": "Gulistan",
    "shahrisabz": "Shahrisabz", "шаҳрисабз": "Shahrisabz",
    "angren": "Angren", "ангрен": "Angren",
    "chirchiq": "Chirchiq", "чирчиқ": "Chirchiq",
    "zarafshon": "Zarafshan", "зарафшон": "Zarafshan",

    # Türkiye
    "istanbul": "Istanbul", "istnbul": "Istanbul", "istambul": "Istanbul", "stambul": "Istanbul", "истанбул": "Istanbul", "стамбул": "Istanbul",
    "ankara": "Ankara", "анкара": "Ankara",
    "izmir": "Izmir", "измир": "Izmir",
    "bursa": "Bursa", "бурса": "Bursa",
    "antalya": "Antalya", "анталья": "Antalya",
    "adana": "Adana", "адана": "Adana",
    "konya": "Konya",
    "gaziantep": "Gaziantep", "antep": "Gaziantep",
    "sanliurfa": "Sanliurfa", "urfa": "Sanliurfa",
    "kayseri": "Kayseri", "eskisehir": "Eskisehir", "samsun": "Samsun",
    "trabzon": "Trabzon", "mersin": "Mersin", "malatya": "Malatya",
    "sivas": "Sivas", "erzurum": "Erzurum", "denizli": "Denizli",

    # Dünya
    "moskva": "Moscow", "moscow": "Moscow", "москва": "Moscow",
    "almaty": "Almaty", "olmaota": "Almaty", "алматы": "Almaty",
    "astana": "Astana", "ostona": "Astana", "астана": "Astana",
    "bishkek": "Bishkek", "бишкек": "Bishkek",
    "dushanbe": "Dushanbe", "душанбе": "Dushanbe",
    "baku": "Baku", "boku": "Baku", "баку": "Baku",
    "makka": "Makkah", "mekke": "Makkah", "mecca": "Makkah",
    "madina": "Medina", "medine": "Medina",
    "dubai": "Dubai", "dubay": "Dubai",
    "london": "London", "лондон": "London",
    "berlin": "Berlin",
    "paris": "Paris", "parij": "Paris",
}

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

# =====================================================================
# ÇİFT KATMANLI KESİNTİSİZ NAMAZ VAKTİ MOTORU
# =====================================================================
async def fetch_prayer_times(city_input: str):
    city_clean = clean_prayer_query(city_input)
    if not city_clean:
        return None, None

    norm = normalize_key(city_clean)
    mapped = CITY_ALIASES.get(norm)

    candidates = []
    if mapped:
        candidates.append(mapped)
    candidates.append(city_clean)
    if not any(c in norm for c in ('uzbekistan', 'turkey', 'turkiya', 'kazakhstan', 'russia')):
        candidates.append(f"{city_clean} Uzbekistan")
        candidates.append(f"{city_clean} Turkey")

    # Benzersiz aday listesi
    unique_candidates = []
    for c in candidates:
        if c.lower() not in [x.lower() for x in unique_candidates]:
            unique_candidates.append(c)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    async with httpx.AsyncClient(timeout=10.0, verify=False, follow_redirects=True) as client:
        # 1. Aşama: Nun.html standart timingsByAddress servisi
        for cand in unique_candidates:
            url = f"https://api.aladhan.com/v1/timingsByAddress?address={urllib.parse.quote(cand)}"
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 200 and "data" in data and "timings" in data["data"]:
                        return data["data"], cand
            except Exception:
                pass

        # 2. Aşama: Koordinat Destekli Arama (Geocoding -> Aladhan Koordinat API)
        for cand in unique_candidates:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(cand)}&count=1&language=en&format=json"
            try:
                geo_resp = await client.get(geo_url, headers=headers)
                if geo_resp.status_code == 200:
                    geo_json = geo_resp.json()
                    results = geo_json.get("results")
                    if results and len(results) > 0:
                        first = results[0]
                        lat = first.get("latitude")
                        lon = first.get("longitude")
                        found_name = first.get("name", cand)

                        coord_url = f"https://api.aladhan.com/v1/timings?latitude={lat}&longitude={lon}"
                        coord_resp = await client.get(coord_url, headers=headers)
                        if coord_resp.status_code == 200:
                            c_json = coord_resp.json()
                            if c_json.get("code") == 200 and "data" in c_json and "timings" in c_json["data"]:
                                return c_json["data"], found_name
            except Exception:
                pass

    return None, None

def format_nun_prayer_card(display_name: str, user_input: str, data: dict, lang: str = 'uz') -> str:
    timings = data.get("timings", {})
    date_info = data.get("date", {})
    readable_date = date_info.get("readable", "")
    greg_date = date_info.get("gregorian", {}).get("date", readable_date)
    hijri = date_info.get("hijri", {})
    hijri_str = f"{hijri.get('day', '')} {hijri.get('month', {}).get('en', '')} {hijri.get('year', '')}".strip()

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
        labels = ["İMSAK", "GÜNEŞ", "ÖĞLE", "İKİNDİ", "AKŞAM", "YATSI"]
        footer = "_Sistem: Canlı AlAdhan API senkronizasyonu_"
        fuzzy_note = f"\n_🎯 Arama: \"{clean_inp}\" ➔ *{clean_disp}* olarak belirlendi._\n" if is_fuzzy else ""
    elif lang == 'ru':
        header = "*NUN PROJECT // ВРЕМЯ НАМАЗА*"
        labels = ["ФАДЖР", "ВОСХОД", "ЗУХР", "АСР", "МАГРИБ", "ИША"]
        footer = "_Система: Данные AlAdhan API онлайн_"
        fuzzy_note = f"\n_🎯 Поиск: \"{clean_inp}\" ➔ *{clean_disp}* определено._\n" if is_fuzzy else ""
    elif lang == 'en':
        header = "*NUN PROJECT // PRAYER TIMES*"
        labels = ["FAJR", "SUNRISE", "DHUHR", "ASR", "MAGHRIB", "ISHA"]
        footer = "_System: Live AlAdhan API sync_"
        fuzzy_note = f"\n_🎯 Search: \"{clean_inp}\" ➔ Predicted as *{clean_disp}*._\n" if is_fuzzy else ""
    else:  # 'uz'
        header = "*NUN PROJECT // NAMOZ VAQTLARI*"
        labels = ["BOMDOD", "QUYOSH", "PESHIN", "ASR", "SHOM", "XUFTON"]
        footer = "_Tizim holati: Jonli AlAdhan API orqali olindi_"
        fuzzy_note = f"\n_🎯 Qidiruv: \"{clean_inp}\" ➔ *{clean_disp}* deb aniqlandi._\n" if is_fuzzy else ""

    date_line = f"📅 `{greg_date}`"
    if hijri_str:
        date_line += f"  •  🌙 `{hijri_str}`"

    card = (
        f"{header}\n"
        f"📍 *[ {clean_disp.upper()} ]*\n"
        f"{date_line}\n"
        f"{fuzzy_note}\n"
        f"┌────────────────────────────┐\n"
        f"  ▫️ *{labels[0]}:*{' ' * max(1, 9 - len(labels[0]))}`{t_fajr}`\n"
        f"  ▫️ *{labels}:*{' ' * max(1, 9 - len(labels))}`{t_sunrise}`\n"
        f"  ▫️ *{labels}:*{' ' * max(1, 9 - len(labels))}`{t_dhuhr}`\n"
        f"  ▫️ *{labels}:*{' ' * max(1, 9 - len(labels))}`{t_asr}`\n"
        f"  ▫️ *{labels[4]}:*{' ' * max(1, 9 - len(labels[4]))}`{t_maghrib}`\n"
        f"  ▫️ *{labels[5]}:*{' ' * max(1, 9 - len(labels[5]))}`{t_isha}`\n"
        f"└────────────────────────────┘\n"
        f"{footer}"
    )
    return card

# =====================================================================
# METİNLER & MENÜ YAPILARI
# =====================================================================
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Nun Botga xush kelibsiz.\n\nKerakli boʻlimni tanlang yoki toʻgʻridan-toʻgʻri shahar nomini yuboring:",
        'menu_title': "📋 Asosiy menyu:",
        'btn_video': "🎬 Video yuklash",
        'btn_prayer': "🕌 Namoz vaqtlari",
        'btn_c2l': "🔤 Krill ➔ Lotin",
        'btn_l2c': "🔤 Lotin ➔ Krill",
        'btn_lang': "🌐 Tilni tanlash",
        'prompt_prayer': "🕌 *NUN PROJECT // NAMOZ VAQTLARI*\n\nNamoz vaqtlarini bilmoqchi boʻlgan shahar nomini yozib yuboring:\n_(Masalan: *Qoʻqon*, *Toshkent*, *Samarqand*, *Istanbul*, *Buxoro*...)_",
        'prompt_c2l': "✍️ Kirill alifbosidagi matnni yuboring, uni Lotin alifbosiga oʻgirib beraman:",
        'prompt_l2c': "✍️ Lotin alifbosidagi matnni yuboring, uni Kirill alifbosiga oʻgirib beraman:",
        'prompt_video': "🔗 Instagram, TikTok, Facebook yoki X (Twitter) havolasini yuboring:",
        'downloading': "⏳ Video yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Fayl hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Xatolik yuz berdi. Qaytadan urinib koʻring.",
        'city_not_found': "❌ Shahar aniqlanmadi. Iltimos, shahar nomini toʻgʻri yozing (Masalan: *Qoʻqon*, *Toshkent*, *Istanbul*).",
        'lang_changed': "✅ Til muvaffaqiyatli oʻzgartirildi!",
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать в Nun Bot.\n\nВыберите действие в меню или отправьте название города:",
        'menu_title': "📋 Главное меню:",
        'btn_video': "🎬 Скачать видео",
        'btn_prayer': "🕌 Время намаза",
        'btn_c2l': "🔤 Кириллица ➔ Латиница",
        'btn_l2c': "🔤 Латиница ➔ Кириллица",
        'btn_lang': "🌐 Сменить язык",
        'prompt_prayer': "🕌 *NUN PROJECT // ВРЕМЯ НАМАЗА*\n\nНапишите название города для получения времени намаза:\n_(Например: *Коканд*, *Ташкент*, *Стамбул*, *Москва*, *Самарканд*...)_",
        'prompt_c2l': "✍️ Отправьте текст на кириллице для перевода в латиницу:",
        'prompt_l2c': "✍️ Отправьте текст на латинице для перевода в кириллицу:",
        'prompt_video': "🔗 Отправьте ссылку из Instagram, TikTok, Facebook или X (Twitter):",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Произошла ошибка. Попробуйте снова.",
        'city_not_found': "❌ Город не распознан. Пожалуйста, напишите заново (Например: *Коканд*, *Ташкент*, *Стамбул*).",
        'lang_changed': "✅ Язык успешно изменен!",
    },
    'en': {
        'welcome': "Hello! Welcome to Nun Bot.\n\nChoose an action from the menu or send a city name:",
        'menu_title': "📋 Main Menu:",
        'btn_video': "🎬 Download Video",
        'btn_prayer': "🕌 Prayer Times",
        'btn_c2l': "🔤 Cyrillic ➔ Latin",
        'btn_l2c': "🔤 Latin ➔ Cyrillic",
        'btn_lang': "🌐 Change Language",
        'prompt_prayer': "🕌 *NUN PROJECT // PRAYER TIMES*\n\nType the city name to get prayer times:\n_(e.g. *Kokand*, *Tashkent*, *Istanbul*, *London*, *Samarkand*...)_",
        'prompt_c2l': "✍️ Send text in Cyrillic to convert into Latin:",
        'prompt_l2c': "✍️ Send text in Latin to convert into Cyrillic:",
        'prompt_video': "🔗 Send a link from Instagram, TikTok, Facebook, or X (Twitter):",
        'downloading': "⏳ Downloading media, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ An error occurred. Please try again.",
        'city_not_found': "❌ City could not be detected. Please try again (e.g. *Kokand*, *Tashkent*, *Istanbul*).",
        'lang_changed': "✅ Language updated successfully!",
    },
    'tr': {
        'welcome': "Merhaba! Nun Bota hoş geldiniz.\n\nAşağıdaki menüden işlem seçebilir veya doğrudan şehir adı yazabilirsiniz:",
        'menu_title': "📋 Ana Menü:",
        'btn_video': "🎬 Video İndir",
        'btn_prayer': "🕌 Namaz Vakitleri",
        'btn_c2l': "🔤 Kiril ➔ Latin",
        'btn_l2c': "🔤 Latin ➔ Kiril",
        'btn_lang': "🌐 Dil Seçimi",
        'prompt_prayer': "🕌 *NUN PROJECT // NAMAZ VAKİTLERİ*\n\nNamaz vakitlerini öğrenmek istediğiniz şehrin adını yazıp gönderin:\n_(Örneğin: *Kokand*, *İstanbul*, *Ankara*, *Taşkent*, *Bursa*...)_",
        'prompt_c2l': "✍️ Latin alfabesine çevirmek istediğiniz Kiril metni gönderin:",
        'prompt_l2c': "✍️ Kiril alfabesine çevirmek istediğiniz Latin metni gönderin:",
        'prompt_video': "🔗 Instagram, TikTok, Facebook veya X (Twitter) linki gönderin:",
        'downloading': "⏳ Medya indiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ Bir hata oluştu. Lütfen tekrar deneyin.",
        'city_not_found': "❌ Şehir bulunamadı. Lütfen tekrar yazın (Örneğin: *Kokand*, *İstanbul*, *Ankara*, *Taşkent*).",
        'lang_changed': "✅ Dil başarıyla değiştirildi!",
    }
}

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

def get_user_lang(user_id, context: ContextTypes.DEFAULT_TYPE) -> str:
    if context.user_data and 'lang' in context.user_data:
        lang = context.user_data['lang']
        if isinstance(lang, str):
            return lang
    return 'uz'

def get_text(user_id, key, context: ContextTypes.DEFAULT_TYPE) -> str:
    lang = get_user_lang(user_id, context)
    return TEXTS.get(lang, TEXTS['uz']).get(key, '')

def get_reply_menu(user_id, context):
    return ReplyKeyboardMarkup([
        [KeyboardButton(get_text(user_id, 'btn_video', context)), KeyboardButton(get_text(user_id, 'btn_prayer', context))],
        [KeyboardButton(get_text(user_id, 'btn_c2l', context)), KeyboardButton(get_text(user_id, 'btn_l2c', context))],
        [KeyboardButton(get_text(user_id, 'btn_lang', context))]
    ], resize_keyboard=True)

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"), InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
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
        ],
        'ru': [
            BotCommand("start", "Запустить бота"),
            BotCommand("menu", "Главное меню"),
            BotCommand("namaz", "Время намаза"),
        ],
        'en': [
            BotCommand("start", "Start the bot"),
            BotCommand("menu", "Main menu"),
            BotCommand("prayer", "Prayer times"),
        ],
        'tr': [
            BotCommand("start", "Botu başlat"),
            BotCommand("menu", "Ana menü"),
            BotCommand("namaz", "Namaz vakitleri"),
        ],
    }
    try:
        cmds = commands_map.get(lang, commands_map['uz'])
        await context.bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=user_id))
    except Exception:
        pass

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_lang = update.effective_user.language_code or 'uz'
    if user_lang.startswith('tr'):
        context.user_data['lang'] = 'tr'
    elif user_lang.startswith('ru'):
        context.user_data['lang'] = 'ru'
    elif user_lang.startswith('en'):
        context.user_data['lang'] = 'en'
    else:
        context.user_data['lang'] = 'uz'

    await update_user_bot_commands(context, update.effective_user.id, context.user_data['lang'])
    await update.message.reply_text(
        "Tilni tanlang / Lütfen dil seçin / Выберите язык / Select language:",
        reply_markup=get_language_keyboard()
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        get_text(user_id, 'menu_title', context),
        reply_markup=get_reply_menu(user_id, context)
    )

async def prayer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_lang = get_user_lang(user_id, context)

    # Doğrudan /namaz Kokand veya /namoz Toshkent şeklinde girildiyse
    if context.args:
        city_query = " ".join(context.args)
        prayer_data, resolved_name = await fetch_prayer_times(city_query)
        if prayer_data:
            disp_name = resolved_name.title() if resolved_name else city_query.title()
            card = format_nun_prayer_card(disp_name, city_query, prayer_data, user_lang)
            try:
                await update.message.reply_text(card, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(card)
            return
        else:
            await update.message.reply_text(get_text(user_id, 'city_not_found', context), parse_mode="Markdown")
            return

    # Sadece /namoz yazıldıysa veya menüden basıldıysa buton olmadan şehir sorulur
    context.user_data['mode'] = 'prayer'
    await update.message.reply_text(
        get_text(user_id, 'prompt_prayer', context),
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # DİL DEĞİŞİMİ
    if data.startswith("lang_"):
        selected_lang = data.split("_", 1)  # 'uz', 'tr', 'ru', 'en'
        context.user_data['lang'] = selected_lang

        try:
            await query.message.delete()
        except Exception:
            pass

        # Sol alttaki Telegram Menü komutlarını anında güncelle
        await update_user_bot_commands(context, user_id, selected_lang)

        # Alt klavye butonlarını anında yeni dilde güncelle
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ {get_text(user_id, 'lang_changed', context)}\n\n{get_text(user_id, 'welcome', context)}",
            reply_markup=get_reply_menu(user_id, context)
        )
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    raw_text = update.message.text.strip()
    user_lang = get_user_lang(user_id, context)

    # 1. Menü Butonları Tıklamaları
    btn_vid = [TEXTS[l]['btn_video'] for l in TEXTS]
    btn_pry = [TEXTS[l]['btn_prayer'] for l in TEXTS]
    btn_c2l = [TEXTS[l]['btn_c2l'] for l in TEXTS]
    btn_l2c = [TEXTS[l]['btn_l2c'] for l in TEXTS]
    btn_lng = [TEXTS[l]['btn_lang'] for l in TEXTS]

    if raw_text in btn_vid:
        context.user_data['mode'] = 'video'
        await update.message.reply_text(get_text(user_id, 'prompt_video', context))
        return

    if raw_text in btn_pry:
        context.user_data['mode'] = 'prayer'
        await update.message.reply_text(
            get_text(user_id, 'prompt_prayer', context),
            parse_mode="Markdown"
        )
        return

    if raw_text in btn_c2l:
        context.user_data['mode'] = 'c2l'
        await update.message.reply_text(get_text(user_id, 'prompt_c2l', context))
        return

    if raw_text in btn_l2c:
        context.user_data['mode'] = 'l2c'
        await update.message.reply_text(get_text(user_id, 'prompt_l2c', context))
        return

    if raw_text in btn_lng:
        await update.message.reply_text(
            "Tilni tanlang / Lütfen dil seçin / Выберите язык / Select language:",
            reply_markup=get_language_keyboard()
        )
        return

    # 2. Medya Linki Kontrolü
    url_match = re.search(r'https?://[^\s]+', raw_text)
    if url_match:
        url = url_match.group(0)
        if is_supported_url(url):
            status_msg = await update.message.reply_text(get_text(user_id, 'downloading', context))
            asyncio.create_task(process_download(status_msg, user_id, chat_id, url, context))
            return

    current_mode = context.user_data.get('mode', 'auto')
    lower_text = raw_text.lower().strip()

    # Kullanıcı tek başına sadece "namaz" veya "namoz" yazdıysa
    if lower_text in ('namoz', 'namaz', 'prayer', 'vaqt', 'vakit'):
        context.user_data['mode'] = 'prayer'
        await update.message.reply_text(
            get_text(user_id, 'prompt_prayer', context),
            parse_mode="Markdown"
        )
        return

    is_prayer_intent = bool(re.search(r'\b(namoz|namaz|prayer|vaqtlari|vakitleri|vaqti|vakti)\b', lower_text))

    # 3. Namaz Vakti Modu veya Açıkça Namaz Sorusu (Örn: "Kokand", "Toshkent", "namoz toshkent")
    if current_mode == 'prayer' or is_prayer_intent:
        prayer_data, resolved_name = await fetch_prayer_times(raw_text)
        if prayer_data:
            disp_name = resolved_name.title() if resolved_name else clean_prayer_query(raw_text).title()
            card = format_nun_prayer_card(disp_name, raw_text, prayer_data, user_lang)
            try:
                await update.message.reply_text(card, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(card)
            context.user_data['mode'] = 'auto'
            return
        else:
            await update.message.reply_text(get_text(user_id, 'city_not_found', context), parse_mode="Markdown")
            return

    # 4. Kiril -> Latin Modu
    if current_mode == 'c2l':
        converted = cyrillic_to_latin(raw_text)
        await update.message.reply_text(f"🔤 *Lotin:*\n\n{converted}", parse_mode="Markdown")
        return

    # 5. Latin -> Kiril Modu
    if current_mode == 'l2c':
        converted = latin_to_cyrillic(raw_text)
        await update.message.reply_text(f"🔤 *Кирилл:*\n\n{converted}", parse_mode="Markdown")
        return

    # 6. Otomatik Algılama: Menüye basmadan sadece bir şehir yazıldıysa (Örn: "Kokand", "Bursa", "Qo'qon")
    words = raw_text.split()
    if 1 <= len(words) <= 3 and not is_supported_url(raw_text):
        prayer_data, resolved_name = await fetch_prayer_times(raw_text)
        if prayer_data:
            disp_name = resolved_name.title() if resolved_name else clean_prayer_query(raw_text).title()
            card = format_nun_prayer_card(disp_name, raw_text, prayer_data, user_lang)
            try:
                await update.message.reply_text(card, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(card)
            return

    # 7. Aksi halde metin çevirisi (Lotin <-> Kirill)
    if is_mostly_cyrillic(raw_text):
        converted = cyrillic_to_latin(raw_text)
        await update.message.reply_text(f"🔤 *Lotin:*\n\n{converted}", parse_mode="Markdown")
    else:
        converted = latin_to_cyrillic(raw_text)
        await update.message.reply_text(f"🔤 *Кирилл:*\n\n{converted}", parse_mode="Markdown")

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN ortam değişkeni eksik!")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("namoz", prayer_command))
    app.add_handler(CommandHandler("namaz", prayer_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Nun Bot aktif; Namaz Vakitleri, Video ve Çeviri hazır!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
