import os
import re
import uuid
import shutil
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

# --- RENDER 7/24 SAĞLIK KONTROL SUNUCUSU ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

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
        'error_general': "❌ Yuklab olishda xatolik yuz berdi. Havolani tekshiring.",
    },
    'ru': {
        'welcome': "Здравствуйте! Отправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "Выберите формат загрузки:",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио (MP3/M4A)",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Ошибка при скачивании. Проверьте ссылку.",
    },
    'en': {
        'welcome': "Hello! Send a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose download format:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio (MP3/M4A)",
        'downloading': "⏳ Downloading media, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ Download failed. Please verify the link.",
    },
    'tr': {
        'welcome': "Merhaba! YouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "Hangi formatta indirmek istersiniz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ses (MP3/M4A)",
        'downloading': "⏳ Medya indiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ İndirme başarısız oldu. Linki kontrol edin veya videonun herkese açık olduğundan emin olun.",
    }
}

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

# --- ÇEREZ (COOKIE) VE PROXY AYARLARI ---
COOKIE_FILE_PATH = None

def setup_cookies():
    global COOKIE_FILE_PATH
    env_file = os.environ.get("COOKIES_FILE")
    if env_file and os.path.exists(env_file):
        COOKIE_FILE_PATH = env_file
        return

    cookies_text = os.environ.get("COOKIES_TEXT")
    if cookies_text:
        path = os.path.join(tempfile.gettempdir(), "cookies.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cookies_text.strip())
        COOKIE_FILE_PATH = path

setup_cookies()

def get_user_lang(user_id, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data and 'lang' in context.user_data:
        return context.user_data['lang']
    return 'tr'

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

def clean_directory(directory: str):
    for f in os.listdir(directory):
        p = os.path.join(directory, f)
        try:
            if os.path.isfile(p):
                os.unlink(p)
        except Exception:
            pass

def get_downloaded_media_file(directory: str):
    valid_files = []
    for f in os.listdir(directory):
        full_path = os.path.join(directory, f)
        if os.path.isfile(full_path):
            if not f.endswith(('.part', '.ytdl', '.temp', '.aria2', '.json', '.jpg', '.jpeg', '.png', '.webp', '.vtt', '.srt')):
                valid_files.append(full_path)
    if not valid_files:
        return None
    valid_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
    return valid_files[0]

async def safe_edit_text(msg, text, reply_markup=None):
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except Exception:
        pass

# --- YOUTUBE İÇİN GELİŞMİŞ VE ÇÖKMEZ İNDİRME ÇEKİRDEĞİ ---
def download_media_sync(url: str, is_audio: bool, download_dir: str):
    is_yt = is_youtube_url(url)
    has_ffmpeg = shutil.which('ffmpeg') is not None
    proxy = os.environ.get("PROXY_URL") or os.environ.get("HTTP_PROXY")

    # Ortak temel ayarlar
    base_opts = {
        'outtmpl': os.path.join(download_dir, 'media_%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 30,
        'retries': 5,
        'fragment_retries': 5,
    }

    if COOKIE_FILE_PATH and os.path.exists(COOKIE_FILE_PATH):
        base_opts['cookiefile'] = COOKIE_FILE_PATH

    if proxy:
        base_opts['proxy'] = proxy

    # 1. YOUTUBE DIŞINDAKİ PLATFORMLAR (Instagram, Facebook, TikTok)
    if not is_yt:
        opts = dict(base_opts)
        opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        opts['format'] = 'bestaudio/best' if is_audio else 'best[ext=mp4]/best'

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Media') if info else 'Media'
            final_file = get_downloaded_media_file(download_dir)
            if not final_file:
                raise RuntimeError("Instagram/Facebook file not found.")
            return final_file, title

    # 2. YOUTUBE İNDİRME STRATEJİSİ (Çok Katmanlı Fallback Sistemi)
    # Format belirleme: FFmpeg varsa yüksek kalite birleştirme, yoksa tek parça MP4
    if has_ffmpeg:
        if is_audio:
            yt_format = 'bestaudio[ext=m4a]/bestaudio/best'
        else:
            yt_format = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best'
    else:
        # FFmpeg yoksa asla format birleştirme isteme, tek parça progressive mp4 iste
        if is_audio:
            yt_format = 'bestaudio[ext=m4a]/bestaudio/best'
        else:
            yt_format = 'best[ext=mp4]/best/18/22'

    # Sunucu IP engellerini aşmak için denenecek bağımsız istemci sıralaması
    client_strategies = [
        ['ios'],                   # Bot tespiti en az olan istemci (PO token istemez)
        ['web_embedded'],          # Gömülü web oynatıcı (Oturum açma şartı aramaz)
        ['mweb'],                  # Mobil web oynatıcı
        ['tv_simply'],             # TV istemcisi
        ['android'],               # Android istemcisi
        ['web'],                   # Standart web
    ]

    last_error = None

    for client in client_strategies:
        clean_directory(download_dir)
        try:
            ydl_opts = dict(base_opts)
            ydl_opts['format'] = yt_format
            if has_ffmpeg and not is_audio:
                ydl_opts['merge_output_format'] = 'mp4'

            # User-Agent'ı sabit vermiyoruz; yt-dlp seçilen istemciye (iOS/Android) uygun UA'yı kendisi belirler
            ydl_opts['extractor_args'] = {
                'youtube': {
                    'player_client': client,
                }
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'Media') if info else 'Media'
                final_file = get_downloaded_media_file(download_dir)
                if final_file:
                    return final_file, title

        except Exception as err:
            last_error = err
            continue

    # Eğer tüm özel istemciler denenip başarısız olduysa varsayılan ayarla son bir deneme yap
    clean_directory(download_dir)
    try:
        ydl_opts = dict(base_opts)
        ydl_opts['format'] = yt_format
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Media') if info else 'Media'
            final_file = get_downloaded_media_file(download_dir)
            if final_file:
                return final_file, title
    except Exception as final_err:
        last_error = final_err

    raise last_error if last_error else RuntimeError("YouTube download failed.")

# --- İNDİRME VE GÖNDERME İŞLEYİCİSİ ---
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
            print(f"Hata Detayı [{url}]: {e}")
            await safe_edit_text(status_msg, get_text(user_id, 'error_general', context))

# --- TELEGRAM ETKİLEŞİM HANDLERS ---
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
        await update.message.reply_text(
            get_text(user_id, 'choose_format', context),
            reply_markup=get_format_keyboard(user_id, token, context)
        )
    else:
        status_msg = await update.message.reply_text(get_text(user_id, 'downloading', context))
        asyncio.create_task(process_download(status_msg, user_id, chat_id, url, False, context))

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN eksik!")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot sorunsuz başlatıldı...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
