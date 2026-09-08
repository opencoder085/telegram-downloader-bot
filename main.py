import os
import re
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

# --- RENDER 7/24 WEB SERVER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- ÇOK DİLLİ METİNLER ---
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Video yuklab beruvchi botga xush kelibsiz.\n\nYouTube, Instagram, TikTok yoki Facebook havolasini yuboring.",
        'choose_format': "Qaysi formatda yuklab olmoqchisiz? (Faqat YouTube uchun)",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ovoz",
        'downloading': "⏳ Yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Video hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Yuklab olishda xatolik yuz berdi. Havolani tekshiring."
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать в загрузчик видео.\n\nОтправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "Выберите формат (Только для YouTube):",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Ошибка загрузки. Проверьте ссылку."
    },
    'en': {
        'welcome': "Hello! Welcome to Video Downloader bot.\n\nSend a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose download format (YouTube only):",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio",
        'downloading': "⏳ Downloading, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ Download failed. Please verify the link."
    },
    'tr': {
        'welcome': "Merhaba! Video İndirme Botuna hoş geldiniz.\n\nYouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "Hangi formatta indirmek istersiniz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ses",
        'downloading': "⏳ İndiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ İndirme başarısız oldu. Linki kontrol edin."
    }
}

user_languages = {}
pending_links = {}

def get_text(user_id, key):
    lang = user_languages.get(user_id, 'tr')
    return TEXTS.get(lang, TEXTS['tr']).get(key, '')

def get_language_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"), InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"), InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")]
    ])

def get_format_keyboard(user_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text(user_id, 'video_btn'), callback_data="dl_video"),
            InlineKeyboardButton(get_text(user_id, 'audio_btn'), callback_data="dl_audio")
        ]
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "Tilni tanlang / Выберите язык / Select language / Lütfen dil seçin:",
        reply_markup=get_language_keyboard()
    )

def is_youtube_url(url):
    return bool(re.search(r'(?:youtube\.com|youtu\.be)', url, re.IGNORECASE))

def download_media_sync(url, is_audio, download_dir):
    out_tmpl = os.path.join(download_dir, 'media.%(ext)s')
    is_youtube = is_youtube_url(url)
    
    # YouTube engellerini aşmak için tarayıcı/mobil istemci sıralaması
    client_configs = ["tv_embedded", "ios", "android", "web"] if is_youtube else [None]
    last_err = None

    for client in client_configs:
        try:
            ydl_opts = {
                'outtmpl': out_tmpl,
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'socket_timeout': 30,
            }

            if client:
                ydl_opts['extractor_args'] = {
                    'youtube': {
                        'player_client': [client],
                        'skip': ['configs', 'webpage']
                    }
                }

            if is_audio:
                # Sunucuyu çökertmemek için dönüştürücü yerine doğrudan saf ses formatını indir
                ydl_opts['format'] = 'm4a/bestaudio/best'
            else:
                if is_youtube:
                    ydl_opts['format'] = 'best[ext=mp4][filesize<45M]/bestvideo[filesize<35M]+bestaudio/best'
                    ydl_opts['merge_output_format'] = 'mp4'
                else:
                    # Instagram, TikTok ve Facebook için tek parça sorunsuz video formatı
                    ydl_opts['format'] = 'best'

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'Media')
                
                files = os.listdir(download_dir)
                if files:
                    target_file = os.path.join(download_dir, files[0])
                    return target_file, title
        except Exception as e:
            last_err = e
            continue

    raise last_err if last_err else RuntimeError("Download failed.")

async def process_download(status_msg, user_id, url, is_audio, context):
    try:
        # Eski uyarıyı İndiriliyor olarak güncelle
        await status_msg.edit_text(get_text(user_id, 'downloading'))
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path, title = await asyncio.to_thread(download_media_sync, url, is_audio, tmp_dir)
            
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > 49.5:
                await status_msg.edit_text(get_text(user_id, 'error_size'))
                return

            await status_msg.edit_text(get_text(user_id, 'uploading'))

            with open(file_path, 'rb') as f:
                if is_audio:
                    await context.bot.send_audio(
                        chat_id=user_id,
                        audio=f,
                        title=title[:60],
                        read_timeout=180,
                        write_timeout=180
                    )
                else:
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=f,
                        caption=f"🎬 {title[:60]}",
                        supports_streaming=True,
                        read_timeout=180,
                        write_timeout=180
                    )
            
            # Başarılı olunca "İndiriliyor" yazısını temizle
            await status_msg.delete()
            
    except Exception as e:
        print(f"Hata detayi: {e}")
        await status_msg.edit_text(get_text(user_id, 'error_general'))
    finally:
        pending_links.pop(user_id, None)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("lang_"):
        user_languages[user_id] = data.split("_")[1]
        await query.edit_message_text(f"✅ {get_text(user_id, 'welcome')}")
        return

    if data in ["dl_video", "dl_audio"]:
        url = pending_links.get(user_id)
        if not url:
            await query.edit_message_text(get_text(user_id, 'error_general'))
            return
        
        # Butona basıldığında işlemleri başlat
        asyncio.create_task(process_download(query.message, user_id, url, data == "dl_audio", context))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    url_pattern = re.compile(r'https?://(?:www\.)?[^\s]+')
    match = url_pattern.search(text)
    
    if match:
        url = match.group(0)
        
        # YOUTUBE İSE: Seçenek Butonlarını Göster
        if is_youtube_url(url):
            pending_links[user_id] = url
            await update.message.reply_text(
                get_text(user_id, 'choose_format'),
                reply_markup=get_format_keyboard(user_id)
            )
        # INSTAGRAM, TIKTOK, FACEBOOK İSE: Sormadan Doğrudan Video İndir
        else:
            status_msg = await update.message.reply_text(get_text(user_id, 'downloading'))
            asyncio.create_task(process_download(status_msg, user_id, url, False, context))
    else:
        await update.message.reply_text(get_text(user_id, 'welcome'))

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN eksik!")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot basariyla calisiyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
