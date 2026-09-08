import os
import re
import asyncio
import tempfile
import threading
import json
import urllib.request
import urllib.parse
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

# --- RENDER 7/24 WEB SUNUCUSU ---
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

# --- 4 DİLLİ METİNLER ---
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Video yuklab beruvchi botga xush kelibsiz.\n\nYouTube, Instagram, TikTok yoki Facebook havolasini yuboring.",
        'choose_format': "YouTube uchun formatni tanlang:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ovoz (MP3)",
        'downloading': "⏳ Tizimlar aylanib o'tilmoqda, kuting...",
        'uploading': "📤 Telegramga yuborilmoqda...",
        'error_size': "⚠️ Video hajmi 50 MB dan katta.",
        'error_general': "❌ Xatolik yuz berdi. Havolani tekshiring."
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать.\n\nОтправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "Выберите формат для YouTube:",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио (MP3)",
        'downloading': "⏳ Обход защиты, подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Файл больше 50 МБ.",
        'error_general': "❌ Ошибка загрузки."
    },
    'en': {
        'welcome': "Hello! Welcome.\n\nSend a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose format for YouTube:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio (MP3)",
        'downloading': "⏳ Bypassing firewalls, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds 50 MB.",
        'error_general': "❌ Download failed."
    },
    'tr': {
        'welcome': "Merhaba! Video İndirme Botuna hoş geldiniz.\n\nYouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "YouTube için format seçin:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ses (MP3)",
        'downloading': "⏳ Güvenlik duvarları aşılıyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya Telegram'ın 50 MB sınırından büyük.",
        'error_general': "❌ Tüm indirme denemeleri başarısız oldu. Link hatalı/gizli olabilir."
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

# --- KATMAN 1: COBALT API HAVUZU ---
def try_cobalt(url, is_audio, download_dir):
    instances = [
        "https://api.cobalt.tools",
        "https://co.wuk.sh",
        "https://cobalt.api.zluo.cc",
        "https://cobalt.kwiatekm.tokyo"
    ]
    payload = {
        "url": url,
        "videoQuality": "720",
        "downloadMode": "audio" if is_audio else "auto"
    }
    req_data = json.dumps(payload).encode('utf-8')
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    stream_url = None
    for inst in instances:
        for endpoint in ["/", "/api/json"]:
            try:
                req = urllib.request.Request(inst.rstrip('/') + endpoint, data=req_data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                    if "url" in data:
                        stream_url = data["url"]
                        break
            except:
                continue
        if stream_url: break

    if not stream_url:
        raise Exception("Cobalt çöktü")

    ext = "mp3" if is_audio else "mp4"
    dest = os.path.join(download_dir, f"media.{ext}")
    req_file = urllib.request.Request(stream_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_file, timeout=60) as resp, open(dest, 'wb') as f:
        while True:
            chunk = resp.read(65536)
            if not chunk: break
            f.write(chunk)
    return dest, "Video/Ses"

# --- KATMAN 2: VKR DOWNLOADER YEDEK SİSTEM ---
def try_vkr(url, is_audio, download_dir):
    api_url = f"https://api.vkrdownloader.co/api?v={urllib.parse.quote(url)}"
    req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode())
        
    dls = data.get("data", {}).get("downloads", [])
    if not dls:
        raise Exception("VKR bos yanit verdi")
        
    stream_url = dls[0].get("url")
    title = data.get("data", {}).get("title", "Video/Ses")
    
    ext = "mp3" if is_audio else "mp4"
    dest = os.path.join(download_dir, f"media.{ext}")
    req_file = urllib.request.Request(stream_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_file, timeout=60) as resp, open(dest, 'wb') as f:
        while True:
            chunk = resp.read(65536)
            if not chunk: break
            f.write(chunk)
    return dest, title

# --- KATMAN 3: GOOGLEBOT MASKELİ YT-DLP SON ÇARE ---
def try_ytdlp(url, is_audio, download_dir):
    out_tmpl = os.path.join(download_dir, 'media.%(ext)s')
    ydl_opts = {
        'outtmpl': out_tmpl,
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'tv_embedded']
            }
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Video/Ses')
        files = os.listdir(download_dir)
        if not files:
            raise Exception("İndirilemedi")
        return os.path.join(download_dir, files[0]), title

# --- AKILLI YENİLMEZ MOTOR SEÇİCİ ---
def smart_engine(url, is_audio, download_dir):
    try:
        return try_cobalt(url, is_audio, download_dir) # 1. Deneme
    except:
        pass
        
    try:
        return try_vkr(url, is_audio, download_dir) # 2. Deneme
    except:
        pass
        
    return try_ytdlp(url, is_audio, download_dir) # 3. Deneme (Son Çare)

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
            
            ext = os.path.splitext(file_path)[1].lower()

            with open(file_path, 'rb') as f:
                if is_audio or ext in ['.mp3', '.m4a']:
                    await context.bot.send_audio(chat_id=user_id, audio=f, title=title[:60], read_timeout=300, write_timeout=300)
                else:
                    await context.bot.send_video(chat_id=user_id, video=f, caption=f"🎬 {title[:60]}", supports_streaming=True, read_timeout=300, write_timeout=300)
            
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
        asyncio.create_task(process_download(query.message, user_id, url, data == "dl_audio", context))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    url_match = re.search(r'https?://(?:www\.)?[^\s]+', text)
    if not url_match:
        await update.message.reply_text(get_text(user_id, 'welcome'))
        return

    url = url_match.group(0)

    # YOUTUBE İSE BUTON ÇIKART, INSTAGRAM/TIKTOK İSE SORMADAN İNDİR
    if is_youtube_url(url):
        pending_links[user_id] = url
        await update.message.reply_text(get_text(user_id, 'choose_format'), reply_markup=get_yt_format_keyboard(user_id))
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
