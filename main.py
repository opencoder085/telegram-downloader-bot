import os
import re
import shutil
import asyncio
import tempfile
import threading
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

# --- RENDER 7/24 SAĞLIK KONTROL SUNUCUSU (PORT BINDING) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot 7/24 Aktif ve Calisiyor!")

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
# ÖZBEKÇE KİRİL <-> LATİN ÇEVİRİ MOTORU (Tam İmla Kuralları)
# =====================================================================
APOSTROPHES = set(["'", "\u2019", "\u2018", "`", "\u02bb", "\u02bc"])
VOWELS_CYR = set("аоуиэеёюяўАОУИЭЕЁЮЯЎ")

MAP_CYR_TO_LAT = {
    '\u0410': 'A', '\u0430': 'a',
    '\u0411': 'B', '\u0431': 'b',
    '\u0412': 'V', '\u0432': 'v',
    '\u0413': 'G', '\u0433': 'g',
    '\u0414': 'D', '\u0434': 'd',
    '\u0416': 'J', '\u0436': 'j',
    '\u0417': 'Z', '\u0437': 'z',
    '\u0418': 'I', '\u0438': 'i',
    '\u0419': 'Y', '\u0439': 'y',
    '\u041a': 'K', '\u043a': 'k',
    '\u049a': 'Q', '\u049b': 'q',
    '\u041b': 'L', '\u043b': 'l',
    '\u041c': 'M', '\u043c': 'm',
    '\u041d': 'N', '\u043d': 'n',
    '\u041e': 'O', '\u043e': 'o',
    '\u041f': 'P', '\u043f': 'p',
    '\u0420': 'R', '\u0440': 'r',
    '\u0421': 'S', '\u0441': 's',
    '\u0422': 'T', '\u0442': 't',
    '\u0423': 'U', '\u0443': 'u',
    '\u0424': 'F', '\u0444': 'f',
    '\u0425': 'X', '\u0445': 'x',
    '\u04b2': 'H', '\u04b3': 'h',
    '\u042d': 'E', '\u044d': 'e',
}

MAP_LAT_TO_CYR = {
    'A': '\u0410', 'a': '\u0430',
    'B': '\u0411', 'b': '\u0431',
    'V': '\u0412', 'v': '\u0432',
    'G': '\u0413', 'g': '\u0433',
    'D': '\u0414', 'd': '\u0434',
    'J': '\u0416', 'j': '\u0436',
    'Z': '\u0417', 'z': '\u0437',
    'I': '\u0418', 'i': '\u0438',
    'Y': '\u0419', 'y': '\u0439',
    'K': '\u041a', 'k': '\u043a',
    'Q': '\u049a', 'q': '\u049b',
    'L': '\u041b', 'l': '\u043b',
    'M': '\u041c', 'm': '\u043c',
    'N': '\u041d', 'n': '\u043d',
    'O': '\u041e', 'o': '\u043e',
    'P': '\u041f', 'p': '\u043f',
    'R': '\u0420', 'r': '\u0440',
    'S': '\u0421', 's': '\u0441',
    'T': '\u0422', 't': '\u0442',
    'U': '\u0423', 'u': '\u0443',
    'F': '\u0424', 'f': '\u0444',
    'X': '\u0425', 'x': '\u0445',
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

        # Сҳ, сҳ ➔ s'h (sh ile karışmaması için tutuk işareti)
        if c in ('С', 'с') and next_char in ('Ҳ', 'ҳ'):
            res = ("S'H" if next_is_upper else "S'h") if is_upper else "s'h"
            result.append(res)
            i += 2
            continue

        # Е, е ➔ Kelime başı / sesli harften sonra Ye, sessizden sonra E
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
            res = ("YO" if next_is_upper else "Yo") if is_upper else "yo"
            result.append(res)
            i += 1
            continue

        if c in ('Ю', 'ю'):
            res = ("YU" if next_is_upper else "Yu") if is_upper else "yu"
            result.append(res)
            i += 1
            continue

        if c in ('Я', 'я'):
            res = ("YA" if next_is_upper else "Ya") if is_upper else "ya"
            result.append(res)
            i += 1
            continue

        if c in ('Ч', 'ч'):
            res = ("CH" if next_is_upper else "Ch") if is_upper else "ch"
            result.append(res)
            i += 1
            continue

        if c in ('Ш', 'ш') or c in ('Щ', 'щ'):
            res = ("SH" if next_is_upper else "Sh") if is_upper else "sh"
            result.append(res)
            i += 1
            continue

        if c in ('Ц', 'ц'):
            res = ("TS" if next_is_upper else "Ts") if is_upper else "ts"
            result.append(res)
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

        # s'h ➔ сҳ
        if c in ('s', 'S') and c_next in APOSTROPHES and c_next2 in ('h', 'H'):
            res = ("СҲ" if c_next2.isupper() else "Сҳ") if c == 'S' else "сҳ"
            result.append(res)
            i += 3
            continue

        # oʻ, o' ➔ ў
        if c in ('o', 'O') and c_next and c_next in APOSTROPHES:
            result.append("Ў" if c == 'O' else "ў")
            i += 2
            continue

        # gʻ, g' ➔ ғ
        if c in ('g', 'G') and c_next and c_next in APOSTROPHES:
            result.append("Ғ" if c == 'G' else "ғ")
            i += 2
            continue

        # sh ➔ ш
        if c in ('s', 'S') and c_next in ('h', 'H'):
            result.append("Ш" if c.isupper() else "ш")
            i += 2
            continue

        # ch ➔ ч
        if c in ('c', 'C') and c_next in ('h', 'H'):
            result.append("Ч" if c.isupper() else "ч")
            i += 2
            continue

        # ts ➔ ц
        if c in ('t', 'T') and c_next in ('s', 'S'):
            result.append("Ц" if c.isupper() else "ц")
            i += 2
            continue

        # yo ➔ ё
        if c in ('y', 'Y') and c_next in ('o', 'O'):
            result.append("Ё" if c.isupper() else "ё")
            i += 2
            continue

        # yu ➔ ю
        if c in ('y', 'Y') and c_next in ('u', 'U'):
            result.append("Ю" if c.isupper() else "ю")
            i += 2
            continue

        # ya ➔ я
        if c in ('y', 'Y') and c_next in ('a', 'A'):
            result.append("Я" if c.isupper() else "я")
            i += 2
            continue

        # ye ➔ е
        if c in ('y', 'Y') and c_next in ('e', 'E'):
            result.append("Е" if c.isupper() else "е")
            i += 2
            continue

        # E / e ➔ Kelime başı/sesliden sonra Э, sessizden sonra Е
        if c in ('e', 'E'):
            is_word_start = (i == 0 or not prev_char.isalpha())
            after_vowel = (prev_char.lower() in 'aouie')
            if is_word_start or after_vowel:
                result.append("Э" if c == 'E' else "э")
            else:
                result.append("Е" if c == 'E' else "е")
            i += 1
            continue

        # Tutuk işareti (ъ)
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
# ÇOK DİLLİ METİNLER VE MENÜ ARAYÜZÜ
# =====================================================================
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Botga xush kelibsiz.\n\nQuyidagi amallardan birini tanlang yoki toʻgʻridan-toʻgʻri havola/matn yuboring:",
        'menu_title': "📋 Asosiy menyu:",
        'btn_video': "🎬 Video yuklash",
        'btn_c2l': "🔤 Krill ➔ Lotin",
        'btn_l2c': "🔤 Lotin ➔ Krill",
        'btn_lang': "🌐 Tilni tanlash",
        'prompt_c2l': "✍️ Kirill alifbosidagi matnni yuboring, uni Lotin alifbosiga oʻgirib beraman:",
        'prompt_l2c': "✍️ Lotin alifbosidagi matnni yuboring, uni Kirill alifbosiga oʻgirib beraman:",
        'prompt_video': "🔗 Instagram, TikTok, Facebook yoki X (Twitter) havolasini yuboring:",
        'downloading': "⏳ Video yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Fayl hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Xatolik yuz berdi. Qaytadan urinib koʻring.",
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать.\n\nВыберите действие в меню или отправьте ссылку/текст:",
        'menu_title': "📋 Главное меню:",
        'btn_video': "🎬 Скачать видео",
        'btn_c2l': "🔤 Кириллица ➔ Латиница",
        'btn_l2c': "🔤 Латиница ➔ Кириллица",
        'btn_lang': "🌐 Сменить язык",
        'prompt_c2l': "✍️ Отправьте узбекский текст на кириллице для перевода в латиницу:",
        'prompt_l2c': "✍️ Отправьте узбекский текст на латинице для перевода в кириллицу:",
        'prompt_video': "🔗 Отправьте ссылку из Instagram, TikTok, Facebook или X (Twitter):",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Произошла ошибка. Попробуйте снова.",
    },
    'en': {
        'welcome': "Hello! Welcome to the bot.\n\nChoose an action from the menu or send a link/text:",
        'menu_title': "📋 Main Menu:",
        'btn_video': "🎬 Download Video",
        'btn_c2l': "🔤 Cyrillic ➔ Latin",
        'btn_l2c': "🔤 Latin ➔ Cyrillic",
        'btn_lang': "🌐 Change Language",
        'prompt_c2l': "✍️ Send Uzbek text in Cyrillic to convert into Latin:",
        'prompt_l2c': "✍️ Send Uzbek text in Latin to convert into Cyrillic:",
        'prompt_video': "🔗 Send a link from Instagram, TikTok, Facebook, or X (Twitter):",
        'downloading': "⏳ Downloading media, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ An error occurred. Please try again.",
    },
    'tr': {
        'welcome': "Merhaba! Bota hoş geldiniz.\n\nAşağıdaki menüden işlem seçebilir veya doğrudan link/metin gönderebilirsiniz:",
        'menu_title': "📋 Ana Menü:",
        'btn_video': "🎬 Video İndir",
        'btn_c2l': "🔤 Kiril ➔ Latin",
        'btn_l2c': "🔤 Latin ➔ Kiril",
        'btn_lang': "🌐 Dil Seçimi",
        'prompt_c2l': "✍️ Latin alfabesine çevirmek istediğiniz Özbekçe Kiril metni gönderin:",
        'prompt_l2c': "✍️ Kiril alfabesine çevirmek istediğiniz Özbekçe Latin metni gönderin:",
        'prompt_video': "🔗 Instagram, TikTok, Facebook veya X (Twitter) linki gönderin:",
        'downloading': "⏳ Medya indiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ Bir hata oluştu. Lütfen tekrar deneyin.",
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
        [KeyboardButton(get_text(user_id, 'btn_video', context))],
        [KeyboardButton(get_text(user_id, 'btn_c2l', context)), KeyboardButton(get_text(user_id, 'btn_l2c', context))],
        [KeyboardButton(get_text(user_id, 'btn_lang', context))]
    ], resize_keyboard=True)

# Dil Seçim Butonları (Inline Keyboard)
def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"), InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"), InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")]
    ])

# =====================================================================
# VİDEO İNDİRME MOTORU (Instagram, TikTok, Facebook, X)
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
# TELEGRAM KOMUTLARI VE MESAJ İŞLEYİCİSİ
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

    user_id = update.effective_user.id
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

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        context.user_data['lang'] = selected_lang
        await query.message.delete()
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ {get_text(user_id, 'welcome', context)}",
            reply_markup=get_reply_menu(user_id, context)
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    text = update.message.text.strip()

    # 1. Menü Butonlarına Basıldığında Mod Değiştirme
    btn_vid = [TEXTS[l]['btn_video'] for l in TEXTS]
    btn_c2l = [TEXTS[l]['btn_c2l'] for l in TEXTS]
    btn_l2c = [TEXTS[l]['btn_l2c'] for l in TEXTS]
    btn_lng = [TEXTS[l]['btn_lang'] for l in TEXTS]

    if text in btn_vid:
        context.user_data['mode'] = 'video'
        await update.message.reply_text(get_text(user_id, 'prompt_video', context))
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

    # 2. Link Kontrolü (Herhangi bir anda link gelirse doğrudan video indirir)
    url_match = re.search(r'https?://[^\s]+', text)
    if url_match:
        url = url_match.group(0)
        if is_supported_url(url):
            status_msg = await update.message.reply_text(get_text(user_id, 'downloading', context))
            asyncio.create_task(process_download(status_msg, user_id, chat_id, url, context))
            return

    # 3. Metin Çevirisi (Kiril ➔ Latin veya Latin ➔ Kiril)
    current_mode = context.user_data.get('mode', 'auto')

    if current_mode == 'c2l':
        # Zorunlu Kiril -> Latin modu
        converted = cyrillic_to_latin(text)
        await update.message.reply_text(f"🔤 Lotin:\n\n{converted}")
        return

    if current_mode == 'l2c':
        # Zorunlu Latin -> Kiril modu
        converted = latin_to_cyrillic(text)
        await update.message.reply_text(f"🔤 Кирилл:\n\n{converted}")
        return

    # 4. Otomatik Algılama Modu (Kullanıcı doğrudan metin yazarsa)
    if is_mostly_cyrillic(text):
        # Kiril yazıldıysa Latinceye çevir
        converted = cyrillic_to_latin(text)
        await update.message.reply_text(f"🔤 Lotin:\n\n{converted}")
    else:
        # Latin yazıldıysa Kirile çevir
        converted = latin_to_cyrillic(text)
        await update.message.reply_text(f"🔤 Кирилл:\n\n{converted}")

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN ortam değişkeni eksik!")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot 7/24 aktif; Video İndirme ve Özbekçe Çeviri hazır!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
