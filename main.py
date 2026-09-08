import os
import re
import asyncio
import tempfile
import threading
import json
import urllib.request
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

# --- RENDER SAĞLIK SUNUCUSU (7/24 AÇIK TUTAR) ---
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

# --- METİNLER (4 DİL) ---
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Video yuklab beruvchi botga xush kelibsiz.\n\nYouTube, Instagram, TikTok yoki Facebook havolasini yuboring.",
        'choose_format': "YouTube uchun formatni tanlang:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ovoz (MP3)",
        'downloading': "⏳ Yuklanmoqda, kuting...",
        'uploading': "📤 Telegramga yuborilmoqda...",
        'error_size': "⚠️ Video hajmi 50 MB dan katta.",
        'error_general': "❌ Xatolik yuz berdi. Havolani tekshiring."
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать.\n\nОтправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "Выберите формат для YouTube:",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио (MP3)",
        'downloading': "⏳ Скачивается, подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Файл больше 50 МБ.",
        'error_general': "❌ Ошибка загрузки."
    },
    'en': {
        'welcome': "Hello! Welcome.\n\nSend a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose format for YouTube:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio (MP3)",
        'downloading': "⏳ Downloading...",
        'uploading': "📤 Uploading...",
        'error_size': "⚠️ File exceeds 50 MB.",
        'error_general': "❌ Download failed."
    },
    'tr': {
        'welcome': "Merhaba! Video İndirme Botuna hoş geldiniz.\n\nYouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "YouTube için format seçin:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ses (MP3)",
        'downloading': "⏳ İndiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya Telegram'ın 50 MB sınırından büyük.",
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

def get_yt_format_keyboard(user_id):
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

# --- MOTOR 1: COBALT API (YOUTUBE İÇİN - FFMPEG GEREKTİRMEZ) ---
def download_youtube_cobalt(url, is_audio, download_dir):
    # En aktif public sunucular (V9 ve V10 sürümleri)
    instances = [
        "https://co.wuk.sh",
        "https://api.cobalt.tools",
        "https://cobalt-api.kwiatekm.tokyo",
        "https://api.wuk.sh"
    ]
    
    # Hem yeni hem eski API sürümlerini destekleyen birleşik komut
    payload = {
        "url": url,
        "vQuality": "720",
        "videoQuality": "720",
        "isAudioOnly": is_audio,
        "downloadMode": "audio" if is_audio else "auto",
        "aFormat": "mp3",
        "audioFormat": "mp3"
    }
    req_data = json.dumps(payload).encode('utf-8')
    
    # Anti-Bot duvarını aşan tarayıcı kimliği
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Origin": "https://cobalt.tools",
        "Referer": "https://cobalt.tools/"
    }

    stream_url = None
    
    for inst in instances:
        for endpoint in ["/", "/api/json"]:
            try:
                target = inst.rstrip('/') + endpoint
                req = urllib.request.Request(target, data=req_data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if "url" in data:
                        stream_url = data["url"]
                        break
            except Exception:
                continue
        if stream_url:
            break

    if not stream_url:
        raise RuntimeError("YouTube baglantisi saglanamadi.")

    ext = "mp3" if is_audio else "mp4"
    dest = os.path.join(download_dir, f"media.{ext}")
    
    # Dosyayı hazır olarak sunucudan çek
    req_file = urllib.request.Request(stream_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_file, timeout=60) as resp, open(dest, 'wb') as f:
        while True:
            chunk = resp.read(16384)
            if not chunk:
                break
            f.write(chunk)

    return dest, "YouTube Media"

# --- MOTOR 2: YT-DLP (INSTAGRAM/TIKTOK İÇİN - FFMPEG GEREKTİRMEZ) ---
def download_social_media(url, download_dir):
    out_tmpl = os.path.join(download_dir, 'media.%(ext)s')
    ydl_opts = {
        'outtmpl': out_tmpl,
        'format': 'best', # ffmpeg birleştirme işlemi istemez, tek parça çeker
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Video')
        files = os.listdir(download_dir)
        if not files:
            raise FileNotFoundError("Dosya inemedi.")
        return os.path.join(download_dir, files[0]), title

def smart_engine(url, is_audio, download_dir):
    if is_youtube_url(url):
        return download_youtube_cobalt(url, is_audio, download_dir)
    else:
        return download_social_media(url, download_dir)

async def process_download(status_msg, user_id, url, is_audio, context):
    try:
        await status_msg.edit_text(get_text(user_id, 'downloading'))
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path, title = await asyncio.to_thread(smart_engine, url, is_audio, tmp_dir)
            
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > 49.5:
                await status_msg.edit_text(get_text(user_id, 'error_size'))
                return

            await status_msg.edit_text(get_text(user_id, 'uploading'))

            # Telegram'ın ağır dosyalarda bağlantıyı koparmaması için 300 saniye mühlet (Timeout)
            with open(file_path, 'rb') as f:
                if is_audio:
                    await context.bot.send_audio(
                        chat_id=user_id,
                        audio=f,
                        title=title[:60],
                        read_timeout=300,
                        write_timeout=300
                    )
                else:
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=f,
                        caption=f"🎬 {title[:60]}",
                        supports_streaming=True,
                        read_timeout=300,
                        write_timeout=300
                    )
            
            await status_msg.delete()
            
    except Exception as e:
        print(f"Hata: {e}")
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
        
        asyncio.create_task(process_download(query.message, user_id, url, data == "dl_audio", context))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    url_match = re.search(r'https?://(?:www\.)?[^\s]+', text)
    if not url_match:
        await update.message.reply_text(get_text(user_id, 'welcome'))
        return

    url = url_match.group(0)

    # 1. YouTube: Seçenek butonları çıkar (Video / Ses)
    if is_youtube_url(url):
        pending_links[user_id] = url
        await update.message.reply_text(
            get_text(user_id, 'choose_format'),
            reply_markup=get_yt_format_keyboard(user_id)
        )
    # 2. Instagram, TikTok, Facebook: Sormadan direkt video indir
    else:
        status_msg = await update.message.reply_text(get_text(user_id, 'downloading'))
        asyncio.create_task(process_download(status_msg, user_id, url, False, context))

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
