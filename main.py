import os
import re
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

# --- RENDER 7/24 SAĞLIK KONTROL SUNUCUSU (PORT BINDING) ---
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

# --- ÇOK DİLLİ METİNLER ---
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Video yuklovchi botga xush kelibsiz.\n\nInstagram, TikTok, Facebook yoki X (Twitter) havolasini yuboring.",
        'downloading': "⏳ Video yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Fayl hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Yuklab olishda xatolik yuz berdi. Havolani tekshiring.",
        'unsupported': "⚠️ Iltimos, faqat Instagram, TikTok, Facebook yoki X (Twitter) havolasini yuboring.",
    },
    'ru': {
        'welcome': "Здравствуйте! Отправьте ссылку из Instagram, TikTok, Facebook или X (Twitter).",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Ошибка при скачивании. Проверьте ссылку.",
        'unsupported': "⚠️ Пожалуйста, отправьте ссылку из Instagram, TikTok, Facebook или X (Twitter).",
    },
    'en': {
        'welcome': "Hello! Send a link from Instagram, TikTok, Facebook, or X (Twitter).",
        'downloading': "⏳ Downloading media, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ Download failed. Please verify the link.",
        'unsupported': "⚠️ Please send links only from Instagram, TikTok, Facebook, or X (Twitter).",
    },
    'tr': {
        'welcome': "Merhaba! Instagram, TikTok, Facebook veya X (Twitter) linki gönderebilirsiniz.",
        'downloading': "⏳ Medya indiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ İndirme başarısız oldu. Linki kontrol edin veya videonun herkese açık olduğundan emin olun.",
        'unsupported': "⚠️ Lütfen yalnızca Instagram, TikTok, Facebook veya X (Twitter) linki gönderin.",
    }
}

# Sunucu RAM koruması için eşzamanlı maksimum 2 indirme
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

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

# Sadece desteklenen 4 platform: Instagram, TikTok, Facebook, X (Twitter)
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
                if os.path.getsize(full_path) > 10 * 1024:  # En az 10 KB
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

# =====================================================================
# DOĞRUDAN İNDİRME MOTORU (Instagram, TikTok, Facebook, X)
# =====================================================================
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
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

# --- İŞLEME VE TELEGRAM'A GÖNDERME ---
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

                # Öncelikle video olarak göndermeyi dene; uyumsuzsa belge (dosya) olarak gönder
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

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        context.user_data['lang'] = selected_lang
        await safe_edit_text(query.message, f"✅ {get_text(user_id, 'welcome', context)}")

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

    # Desteklenmeyen bir link gelirse bilgilendir
    if not is_supported_url(url):
        await update.message.reply_text(get_text(user_id, 'unsupported', context))
        return

    # Sormadan anında doğrudan indirme başlatılır
    status_msg = await update.message.reply_text(get_text(user_id, 'downloading', context))
    asyncio.create_task(process_download(status_msg, user_id, chat_id, url, context))

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN ortam değişkeni eksik!")

    # Render 7/24 sağlık sunucusu arka planda başlatılır
    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot 7/24 aktif; Instagram, TikTok, Facebook ve X (Twitter) hazır!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
