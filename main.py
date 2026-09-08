import os
import re
import json
import shutil
import asyncio
import tempfile
import threading
import urllib.request
import urllib.error
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
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

# --- RENDER 7/24 SAĞLIK KONTROL SUNUCUSU ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot 7/24 Aktif!")

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
# NAMAZ VAKİTLERİ MOTORU & MİNİMALİST KART TASARIMI
# =====================================================================
CITY_ALIASES = {
    'toshkent': 'Tashkent, Uzbekistan', 'тошкент': 'Tashkent, Uzbekistan',
    'samarqand': 'Samarkand, Uzbekistan', 'самарқанд': 'Samarkand, Uzbekistan',
    'buxoro': 'Bukhara, Uzbekistan', 'бухоро': 'Bukhara, Uzbekistan',
    'andijon': 'Andijan, Uzbekistan', 'андижон': 'Andijan, Uzbekistan',
    'namangan': 'Namangan, Uzbekistan', 'наманган': 'Namangan, Uzbekistan',
    'fargona': 'Fergana, Uzbekistan', 'фаргона': 'Fergana, Uzbekistan',
    'qarshi': 'Qarshi, Uzbekistan', 'қарши': 'Qarshi, Uzbekistan',
    'nukus': 'Nukus, Uzbekistan', 'нукус': 'Nukus, Uzbekistan',
    'urganch': 'Urgench, Uzbekistan', 'урганч': 'Urgench, Uzbekistan',
    'jizzax': 'Jizzakh, Uzbekistan', 'жиззах': 'Jizzakh, Uzbekistan',
    'navoiy': 'Navoiy, Uzbekistan', 'навоий': 'Navoiy, Uzbekistan',
    'termiz': 'Termez, Uzbekistan', 'термиз': 'Termez, Uzbekistan',
    'istanbul': 'Istanbul, Turkey', 'истанбул': 'Istanbul, Turkey',
    'ankara': 'Ankara, Turkey', 'анкара': 'Ankara, Turkey',
    'izmir': 'Izmir, Turkey', 'измир': 'Izmir, Turkey',
    'bursa': 'Bursa, Turkey', 'бурса': 'Bursa, Turkey',
    'moskva': 'Moscow, Russia', 'москва': 'Moscow, Russia',
}

def normalize_city_name(city_str: str) -> str:
    cleaned = city_str.strip().replace('İ', 'i').replace('ı', 'i').lower()
    cleaned = re.sub(r"['’`ʻʼ]", "", cleaned)
    return CITY_ALIASES.get(cleaned, city_str.strip())

def fetch_prayer_times(city_input: str):
    normalized = normalize_city_name(city_input)
    encoded = urllib.parse.quote(normalized)
    url = f"https://api.aladhan.com/v1/timingsByAddress?address={encoded}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("code") == 200 and "data" in data:
                return data["data"]
    except Exception as e:
        print(f"Namaz API Hatasi [{city_input}]: {e}")
    return None

def clean_time(time_str: str) -> str:
    return time_str.split()[0] if time_str else "--:--"

def format_prayer_card(city_name: str, data: dict, lang: str = 'uz') -> str:
    timings = data.get("timings", {})
    date_info = data.get("date", {})
    greg_date = date_info.get("gregorian", {}).get("date", date_info.get("readable", ""))
    hijri = date_info.get("hijri", {})
    hijri_str = f"{hijri.get('day', '')} {hijri.get('month', {}).get('en', '')} {hijri.get('year', '')}"
    city = city_name.strip().title()

    fajr = clean_time(timings.get("Fajr"))
    sunrise = clean_time(timings.get("Sunrise"))
    dhuhr = clean_time(timings.get("Dhuhr"))
    asr = clean_time(timings.get("Asr"))
    maghrib = clean_time(timings.get("Maghrib"))
    isha = clean_time(timings.get("Isha"))

    if lang == 'tr':
        return (
            f"🕌 *NAMAZ VAKİTLERİ* — *{city}*\n"
            f"🗓 `{greg_date}`  •  🌙 `{hijri_str}`\n\n"
            f"┌─────────────────────────┐\n"
            f"  ▫️ *İmsak:*         `{fajr}`\n"
            f"  ▫️ *Güneş:*         `{sunrise}`\n"
            f"  ▫️ *Öğle:*          `{dhuhr}`\n"
            f"  ▫️ *İkindi:*        `{asr}`\n"
            f"  ▫️ *Akşam:*         `{maghrib}`\n"
            f"  ▫️ *Yatsı:*         `{isha}`\n"
            f"└─────────────────────────┘\n"
            f"_Güncel astronomik hesaplama ile alınmıştır._"
        )
    elif lang == 'ru':
        return (
            f"🕌 *ВРЕМЯ НАМАЗА* — *{city}*\n"
            f"🗓 `{greg_date}`  •  🌙 `{hijri_str}`\n\n"
            f"┌─────────────────────────┐\n"
            f"  ▫️ *Фаджр (Утро):*    `{fajr}`\n"
            f"  ▫️ *Восход:*         `{sunrise}`\n"
            f"  ▫️ *Зухр (Обед):*     `{dhuhr}`\n"
            f"  ▫️ *Аср:*            `{asr}`\n"
            f"  ▫️ *Магриб:*         `{maghrib}`\n"
            f"  ▫️ *Иша (Ночь):*      `{isha}`\n"
            f"└─────────────────────────┘\n"
            f"_Точные астрономические данные._"
        )
    elif lang == 'en':
        return (
            f"🕌 *PRAYER TIMES* — *{city}*\n"
            f"🗓 `{greg_date}`  •  🌙 `{hijri_str}`\n\n"
            f"┌─────────────────────────┐\n"
            f"  ▫️ *Fajr:*           `{fajr}`\n"
            f"  ▫️ *Sunrise:*        `{sunrise}`\n"
            f"  ▫️ *Dhuhr:*          `{dhuhr}`\n"
            f"  ▫️ *Asr:*            `{asr}`\n"
            f"  ▫️ *Maghrib:*        `{maghrib}`\n"
            f"  ▫️ *Isha:*           `{isha}`\n"
            f"└─────────────────────────┘\n"
            f"_Times calculated via accurate astronomical models._"
        )
    else:  # 'uz'
        return (
            f"🕌 *NAMOZ VAQTLARI* — *{city}*\n"
            f"🗓 `{greg_date}`  •  🌙 `{hijri_str}`\n\n"
            f"┌─────────────────────────┐\n"
            f"  ▫️ *Bomdod (Tong):*   `{fajr}`\n"
            f"  ▫️ *Quyosh:*         `{sunrise}`\n"
            f"  ▫️ *Peshin:*         `{dhuhr}`\n"
            f"  ▫️ *Asr:*            `{asr}`\n"
            f"  ▫️ *Shom:*           `{maghrib}`\n"
            f"  ▫️ *Xufton:*         `{isha}`\n"
            f"└─────────────────────────┘\n"
            f"_Aniq astronomik hisob-kitoblar asosida._"
        )

# =====================================================================
# METİNLER & MENÜ KLAVYELERİ
# =====================================================================
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Botga xush kelibsiz.\n\nQuyidagi menyudan kerakli boʻlimni tanlang yoki havola/matn yuboring:",
        'menu_title': "📋 Asosiy menyu:",
        'btn_video': "🎬 Video yuklash",
        'btn_prayer': "🕌 Namoz vaqtlari",
        'btn_c2l': "🔤 Krill ➔ Lotin",
        'btn_l2c': "🔤 Lotin ➔ Krill",
        'btn_lang': "🌐 Tilni tanlash",
        'prompt_prayer': "🕌 *Namoz vaqtlari*\n\nQaysi shahar uchun namoz vaqtlarini bilmoqchisiz?\nQuyidagi tugmalardan birini bosing yoki shahar nomini yozib yuboring (masalan: *Toshkent*, *Samarqand*, *Istanbul*):",
        'prompt_c2l': "✍️ Kirill alifbosidagi matnni yuboring, uni Lotin alifbosiga oʻgirib beraman:",
        'prompt_l2c': "✍️ Lotin alifbosidagi matnni yuboring, uni Kirill alifbosiga oʻgirib beraman:",
        'prompt_video': "🔗 Instagram, TikTok, Facebook yoki X (Twitter) havolasini yuboring:",
        'downloading': "⏳ Video yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Fayl hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Xatolik yuz berdi. Qaytadan urinib koʻring.",
        'city_not_found': "❌ Shahar topilmadi. Iltimos, nomini toʻgʻri yozing (masalan: *Toshkent*, *Istanbul*, *Moskva*).",
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать.\n\nВыберите действие в меню или отправьте ссылку/текст:",
        'menu_title': "📋 Главное меню:",
        'btn_video': "🎬 Скачать видео",
        'btn_prayer': "🕌 Время намаза",
        'btn_c2l': "🔤 Кириллица ➔ Латиница",
        'btn_l2c': "🔤 Латиница ➔ Кириллица",
        'btn_lang': "🌐 Сменить язык",
        'prompt_prayer': "🕌 *Время намаза*\n\nДля какого города вы хотите узнать время намаза?\nНажмите кнопку ниже или напишите название города (например: *Ташкент*, *Самарканд*, *Москва*, *Стамбул*):",
        'prompt_c2l': "✍️ Отправьте текст на кириллице для перевода в латиницу:",
        'prompt_l2c': "✍️ Отправьте текст на латинице для перевода в кириллицу:",
        'prompt_video': "🔗 Отправьте ссылку из Instagram, TikTok, Facebook или X (Twitter):",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Произошла ошибка. Попробуйте снова.",
        'city_not_found': "❌ Город не найден. Пожалуйста, напишите правильное название (например: *Ташкент*, *Москва*, *Стамбул*).",
    },
    'en': {
        'welcome': "Hello! Welcome to the bot.\n\nChoose an action from the menu or send a link/text:",
        'menu_title': "📋 Main Menu:",
        'btn_video': "🎬 Download Video",
        'btn_prayer': "🕌 Prayer Times",
        'btn_c2l': "🔤 Cyrillic ➔ Latin",
        'btn_l2c': "🔤 Latin ➔ Cyrillic",
        'btn_lang': "🌐 Change Language",
        'prompt_prayer': "🕌 *Prayer Times*\n\nWhich city do you want to get prayer times for?\nTap a button below or type a city name (e.g. *Tashkent*, *Istanbul*, *London*):",
        'prompt_c2l': "✍️ Send text in Cyrillic to convert into Latin:",
        'prompt_l2c': "✍️ Send text in Latin to convert into Cyrillic:",
        'prompt_video': "🔗 Send a link from Instagram, TikTok, Facebook, or X (Twitter):",
        'downloading': "⏳ Downloading media, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ An error occurred. Please try again.",
        'city_not_found': "❌ City not found. Please enter a valid city name (e.g. *Tashkent*, *Istanbul*, *London*).",
    },
    'tr': {
        'welcome': "Merhaba! Bota hoş geldiniz.\n\nAşağıdaki menüden işlem seçebilir veya doğrudan link/metin gönderebilirsiniz:",
        'menu_title': "📋 Ana Menü:",
        'btn_video': "🎬 Video İndir",
        'btn_prayer': "🕌 Namaz Vakitleri",
        'btn_c2l': "🔤 Kiril ➔ Latin",
        'btn_l2c': "🔤 Latin ➔ Kiril",
        'btn_lang': "🌐 Dil Seçimi",
        'prompt_prayer': "🕌 *Namaz Vakitleri*\n\nHangi şehrin namaz vakitlerini öğrenmek istiyorsunuz?\nAşağıdaki butonlardan birine dokunun veya şehir adı yazın (örneğin: *İstanbul*, *Ankara*, *Taşkent*):",
        'prompt_c2l': "✍️ Latin alfabesine çevirmek istediğiniz Kiril metni gönderin:",
        'prompt_l2c': "✍️ Kiril alfabesine çevirmek istediğiniz Latin metni gönderin:",
        'prompt_video': "🔗 Instagram, TikTok, Facebook veya X (Twitter) linki gönderin:",
        'downloading': "⏳ Medya indiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ Bir hata oluştu. Lütfen tekrar deneyin.",
        'city_not_found': "❌ Şehir bulunamadı. Lütfen geçerli bir şehir adı girin (örneğin: *İstanbul*, *Ankara*, *Taşkent*).",
    }
}

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

def get_user_lang(user_id, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data and 'lang' in context.user_data:
        return context.user_data['lang']
    return 'tr'

def get_text(user_id, key, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(user_id, context)
    return TEXTS.get(lang, TEXTS['tr']).get(key, '')

# Kalıcı Alt Menü (Reply Keyboard)
def get_reply_menu(user_id, context):
    return ReplyKeyboardMarkup([
        [KeyboardButton(get_text(user_id, 'btn_video', context)), KeyboardButton(get_text(user_id, 'btn_prayer', context))],
        [KeyboardButton(get_text(user_id, 'btn_c2l', context)), KeyboardButton(get_text(user_id, 'btn_l2c', context))],
        [KeyboardButton(get_text(user_id, 'btn_lang', context))]
    ], resize_keyboard=True)

# Dil Seçim Butonları
def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"), InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"), InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")]
    ])

# Hızlı Şehir Seçim Butonları
def get_quick_cities_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📍 Toshkent", callback_data="city_Tashkent"),
            InlineKeyboardButton("📍 Samarqand", callback_data="city_Samarkand"),
            InlineKeyboardButton("📍 Buxoro", callback_data="city_Bukhara"),
        ],
        [
            InlineKeyboardButton("📍 İstanbul", callback_data="city_Istanbul"),
            InlineKeyboardButton("📍 Ankara", callback_data="city_Ankara"),
            InlineKeyboardButton("📍 Moskva", callback_data="city_Moscow"),
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
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
# TELEGRAM HANDLERS
# =====================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_lang = update.effective_user.language_code or 'tr'
    if user_lang.startswith('uz'):
        context.user_data['lang'] = 'uz'
    elif user_lang.startswith('ru'):
        context.user_data['lang'] = 'ru'
    elif user_lang.startswith('en'):
        context.user_data['lang'] = 'en'
    else:
        context.user_data['lang'] = 'tr'

    await update.message.reply_text(
        "Tilni tanlang / Выберите язык / Select language / Lütfen dil seçin:",
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

    # Eğer /namaz İstanbul veya /namoz Toshkent gibi argüman yazılmışsa
    if context.args:
        city_query = " ".join(context.args)
        data = await asyncio.to_thread(fetch_prayer_times, city_query)
        if data:
            card = format_prayer_card(city_query, data, user_lang)
            await update.message.reply_text(card, parse_mode="Markdown")
            return
        else:
            await update.message.reply_text(get_text(user_id, 'city_not_found', context), parse_mode="Markdown")
            return

    # Argümansız çağrıldıysa hızlı seçim menüsü aç
    context.user_data['mode'] = 'prayer'
    await update.message.reply_text(
        get_text(user_id, 'prompt_prayer', context),
        reply_markup=get_quick_cities_keyboard(),
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # Dil seçimi
    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        context.user_data['lang'] = selected_lang
        await query.message.delete()
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ {get_text(user_id, 'welcome', context)}",
            reply_markup=get_reply_menu(user_id, context)
        )
        return

    # Hızlı Şehir Butonuna Dokunulduğunda
    if data.startswith("city_"):
        city_name = data.split("_")[1]
        user_lang = get_user_lang(user_id, context)
        prayer_data = await asyncio.to_thread(fetch_prayer_times, city_name)
        if prayer_data:
            card = format_prayer_card(city_name, prayer_data, user_lang)
            await query.message.reply_text(card, parse_mode="Markdown")
        else:
            await query.message.reply_text(get_text(user_id, 'city_not_found', context), parse_mode="Markdown")
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    text = update.message.text.strip()
    user_lang = get_user_lang(user_id, context)

    # 1. Menü Buton Kontrolleri
    btn_vid = [TEXTS[l]['btn_video'] for l in TEXTS]
    btn_pry = [TEXTS[l]['btn_prayer'] for l in TEXTS]
    btn_c2l = [TEXTS[l]['btn_c2l'] for l in TEXTS]
    btn_l2c = [TEXTS[l]['btn_l2c'] for l in TEXTS]
    btn_lng = [TEXTS[l]['btn_lang'] for l in TEXTS]

    if text in btn_vid:
        context.user_data['mode'] = 'video'
        await update.message.reply_text(get_text(user_id, 'prompt_video', context))
        return

    if text in btn_pry:
        context.user_data['mode'] = 'prayer'
        await update.message.reply_text(
            get_text(user_id, 'prompt_prayer', context),
            reply_markup=get_quick_cities_keyboard(),
            parse_mode="Markdown"
        )
        return

    if text in btn_c2l:
        context.user_data['mode'] = 'c2l'
        await update.message.reply_text(get_text(user_id, 'prompt_c2l', context))
        return

    if text in btn_l2c:
        context.user_data['mode'] = 'l2c'
        await update.message.reply_text(get_text(user_id, 'prompt_l2c', context))
        return

    if text in btn_lng:
        await update.message.reply_text(
            "Tilni tanlang / Выберите язык / Select language / Lütfen dil seçin:",
            reply_markup=get_language_keyboard()
        )
        return

    # 2. Link Kontrolü (Herhangi bir anda link gelirse doğrudan video indir)
    url_match = re.search(r'https?://[^\s]+', text)
    if url_match:
        url = url_match.group(0)
        if is_supported_url(url):
            status_msg = await update.message.reply_text(get_text(user_id, 'downloading', context))
            asyncio.create_task(process_download(status_msg, user_id, chat_id, url, context))
            return

    current_mode = context.user_data.get('mode', 'auto')

    # 3. Namaz Vakti Modu (Kullanıcı şehir adı yazarsa)
    if current_mode == 'prayer':
        prayer_data = await asyncio.to_thread(fetch_prayer_times, text)
        if prayer_data:
            card = format_prayer_card(text, prayer_data, user_lang)
            await update.message.reply_text(card, parse_mode="Markdown")
            context.user_data['mode'] = 'auto'
            return
        else:
            await update.message.reply_text(get_text(user_id, 'city_not_found', context), parse_mode="Markdown")
            return

    # 4. Kiril -> Latin Modu
    if current_mode == 'c2l':
        converted = cyrillic_to_latin(text)
        await update.message.reply_text(f"🔤 *Lotin:*\n\n{converted}", parse_mode="Markdown")
        return

    # 5. Latin -> Kiril Modu
    if current_mode == 'l2c':
        converted = latin_to_cyrillic(text)
        await update.message.reply_text(f"🔤 *Кирилл:*\n\n{converted}", parse_mode="Markdown")
        return

    # 6. Otomatik Akıllı Algılama
    # Eğer tek kelimelik bilinen bir şehir adı yazılmışsa namaz vaktini ver
    norm_city = normalize_city_name(text)
    if norm_city != text or text.lower() in ['istanbul', 'toshkent', 'ankara', 'bursa', 'izmir', 'samarqand', 'buxoro', 'moskva']:
        data = await asyncio.to_thread(fetch_prayer_times, text)
        if data:
            card = format_prayer_card(text, data, user_lang)
            await update.message.reply_text(card, parse_mode="Markdown")
            return

    # Aksi halde harf çevirisi yap
    if is_mostly_cyrillic(text):
        converted = cyrillic_to_latin(text)
        await update.message.reply_text(f"🔤 *Lotin:*\n\n{converted}", parse_mode="Markdown")
    else:
        converted = latin_to_cyrillic(text)
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

    print("Bot 7/24 aktif; Video, Namaz Vakitleri ve Özbekçe Çeviri hazır!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
