import os
import re
import uuid
import asyncio
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# --- RENDER 7/24 WEB SERVER (PORT BINDING & HEALTH CHECK) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- ÇOK DİLLİ METİNLER ---
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Video yuklovchi botga xush kelibsiz.\n\nYouTube, Instagram, TikTok yoki Facebook havolasini yuboring.",
        'choose_format': "Qaysi formatda yuklab olmoqchisiz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ovoz (MP3/M4A)",
        'downloading': "⏳ Video yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Fayl hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Yuklab olishda xatolik yuz berdi. Havolani tekshiring yoki video maxfiy emasligiga ishonch hosil qiling.",
        'select_lang': "Tilni tanlang:"
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать в загрузчик видео.\n\nОтправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "Выберите формат загрузки:",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио (MP3/M4A)",
        'downloading': "⏳ Загрузка началась, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Ошибка при скачивании. Проверьте ссылку или настройки приватности видео.",
        'select_lang': "Выберите язык:"
    },
    'en': {
        'welcome': "Hello! Welcome to the Video Downloader bot.\n\nSend a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose download format:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio (MP3/M4A)",
        'downloading': "⏳ Downloading media, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB bot upload limit.",
        'error_general': "❌ Download failed. Please verify the link or check if the post is public.",
        'select_lang': "Select language:"
    },
    'tr': {
        'welcome': "Merhaba! Video İndirme Botuna hoş geldiniz.\n\nYouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "Hangi formatta indirmek istersiniz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ses (MP3/M4A)",
        'downloading': "⏳ Medya indiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ İndirme başarısız oldu. Linki kontrol edin veya videonun herkese açık olduğundan emin olun.",
        'select_lang': "Lütfen bir dil seçin:"
    }
}

# Eşzamanlı maksimum indirme sayısı (Render belleğini korumak için)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

# --- ÇEREZ (COOKIE) YÖNETİMİ ---
COOKIE_FILE_PATH = None

def setup_cookies():
    global COOKIE_FILE_PATH
    # 1. Dosya yolu verilmişse
    env_file = os.environ.get("COOKIES_FILE")
    if env_file and os.path.exists(env_file):
        COOKIE_FILE_PATH = env_file
        return

    # 2. Ortam değişkeni olarak metin halinde verilmişse
    cookies_text = os.environ.get("COOKIES_TEXT")
    if cookies_text:
        path = os.path.join(tempfile.gettempdir(), "cookies.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cookies_text.strip())
        COOKIE_FILE_PATH = path

setup_cookies()

# --- YARDIMCI FONKSİYONLAR ---
def get_user_lang(user_id, context: ContextTypes.DEFAULT_TYPE, fallback='tr'):
    if context.user_data and 'lang' in context.user_data:
        return context.user_data['lang']
    return fallback

def get_text(user_id, key, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(user_id, context)
    return TEXTS.get(lang, TEXTS['tr']).get(key, '')

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"), InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"), InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")]
    ])

def get_format_keyboard(user_id, token, context):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text(user_id, 'video_btn', context), callback_data=f"dl_video:{token}"),
            InlineKeyboardButton(get_text(user_id, 'audio_btn', context), callback_data=f"dl_audio:{token}")
        ]
    ])

def is_youtube_url(url: str) -> bool:
    return bool(re.search(r'(?:youtube\.com/(?:watch|shorts|live|embed)|youtu\.be/)', url, re.IGNORECASE))

def is_supported_url(url: str) -> bool:
    patterns = [
        r'(?:youtube\.com|youtu\.be)',
        r'(?:instagram\.com)',
        r'(?:facebook\.com|fb\.watch|fb\.gg)',
        r'(?:tiktok\.com)'
    ]
    return any(re.search(p, url, re.IGNORECASE) for p in patterns)

def get_downloaded_media_file(directory: str):
    valid_files = []
    for f in os.listdir(directory):
        full_path = os.path.join(directory, f)
        if os.path.isfile(full_path):
            # Geçici veya meta dosyaları atla
            if not f.endswith(('.part', '.ytdl', '.temp', '.aria2', '.json', '.jpg', '.jpeg', '.png', '.webp', '.vtt', '.srt')):
                valid_files.append(full_path)
    if not valid_files:
        return None
    # Boyuta göre sıralayıp en büyük olan asıl medyayı al
    valid_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
    return valid_files[0]

async def safe_edit_text(msg, text, reply_markup=None):
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except Exception:
        pass

# --- İNDİRME ÇEKİRDEĞİ ---
def download_media_sync(url: str, is_audio: bool, download_dir: str):
    is_yt = is_youtube_url(url)
    out_tmpl = os.path.join(download_dir, 'media_%(id)s.%(ext)s')

    ydl_opts = {
        'outtmpl': out_tmpl,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate',
        },
    }

    if COOKIE_FILE_PATH and os.path.exists(COOKIE_FILE_PATH):
        ydl_opts['cookiefile'] = COOKIE_FILE_PATH

    if is_yt:
        # YouTube için en kararlı mobil/web istemci sırası
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': ['android', 'ios', 'web'],
            }
        }
        if is_audio:
            ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
        else:
            ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best'
            ydl_opts['merge_output_format'] = 'mp4'
    else:
        # Instagram, TikTok ve Facebook için direkt format
        if is_audio:
            ydl_opts['format'] = 'bestaudio/best'
        else:
            ydl_opts['format'] = 'best[ext=mp4]/best'

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title') if info else 'Media'
        if not title:
            title = 'Media'

        final_file = get_downloaded_media_file(download_dir)
        if not final_file:
            raise RuntimeError("Downloaded file not found in directory.")

        return final_file, title

async def process_download(status_msg, user_id, chat_id, url, is_audio, context):
    async with DOWNLOAD_SEMAPHORE:
        try:
            await safe_edit_text(status_msg, get_text(user_id, 'downloading', context))

            with tempfile.TemporaryDirectory() as tmp_dir:
                file_path, title = await asyncio.to_thread(download_media_sync, url, is_audio, tmp_dir)

                size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if size_mb > 49.5:
                    await safe_edit_text(status_msg, get_text(user_id, 'error_size', context))
                    return

                await safe_edit_text(status_msg, get_text(user_id, 'uploading', context))

                clean_title = re.sub(r'[\\/*?:"<>|]', '', title)[:60]

                if is_audio:
                    with open(file_path, 'rb') as f:
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=f,
                            title=clean_title,
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=60,
                        )
                else:
                    # Önce video olarak göndermeyi dene, uyumsuz codec durumunda dosya olarak gönder
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
            print(f"Download Error [{url}]: {e}")
            await safe_edit_text(status_msg, get_text(user_id, 'error_general', context))

# --- TELEGRAM HANDLERS ---
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

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Tilni tanlang / Выберите язык / Select language / Lütfen dil seçin:",
        reply_markup=get_language_keyboard()
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data

    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        context.user_data['lang'] = selected_lang
        await safe_edit_text(query.message, f"✅ {get_text(user_id, 'welcome', context)}")
        return

    if ":" in data:
        action, token = data.split(":", 1)
        pending_links = context.user_data.get('pending_links', {})
        url = pending_links.get(token)

        if not url:
            await safe_edit_text(query.message, get_text(user_id, 'error_general', context))
            return

        is_audio = (action == "dl_audio")
        asyncio.create_task(process_download(query.message, user_id, chat_id, url, is_audio, context))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    text = update.message.text.strip()

    url_match = re.search(r'https?://[^\s]+', text)
    if not url_match:
        await update.message.reply_text(get_text(user_id, 'welcome', context))
        return

    url = url_match.group(0)

    if not is_supported_url(url):
        await update.message.reply_text(get_text(user_id, 'welcome', context))
        return

    token = uuid.uuid4().hex[:8]
    if 'pending_links' not in context.user_data:
        context.user_data['pending_links'] = {}
    context.user_data['pending_links'][token] = url

    if is_youtube_url(url):
        # YouTube için Video veya Ses format seçimi sun
        await update.message.reply_text(
            get_text(user_id, 'choose_format', context),
            reply_markup=get_format_keyboard(user_id, token, context)
        )
    else:
        # Instagram, Facebook, TikTok için doğrudan video indirmeye başla
        status_msg = await update.message.reply_text(get_text(user_id, 'downloading', context))
        asyncio.create_task(process_download(status_msg, user_id, chat_id, url, False, context))

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("Lütfen BOT_TOKEN ortam değişkenini tanımlayın!")

    # 7/24 Render Uptime için arka plan sunucusu
    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("lang", language_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot aktif ve çalışıyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
