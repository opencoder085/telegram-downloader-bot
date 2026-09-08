import os
import re
import asyncio
import tempfile
import threading
import urllib.request
import json
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

# --- RENDER 7/24 SAĞLIK KONTROLÜ ---
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
        'choose_format': "Qaysi formatda yuklab olmoqchisiz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ovoz (MP3)",
        'downloading': "⏳ Yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Video hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Yuklab olishda xatolik yuz berdi. Havolani tekshiring."
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать в загрузчик видео.\n\nОтправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "В каком формате хотите скачать?",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио (MP3)",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Ошибка загрузки. Проверьте ссылку."
    },
    'en': {
        'welcome': "Hello! Welcome to Video Downloader bot.\n\nSend a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose download format:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio (MP3)",
        'downloading': "⏳ Downloading, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ Download failed. Please verify the link."
    },
    'tr': {
        'welcome': "Merhaba! Video İndirme Botuna hoş geldiniz.\n\nYouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "Hangi formatta indirmek istersiniz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ses (MP3)",
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

# --- YOUTUBE İÇİN COBALT MOTORU (IP ENGELİNİ AŞAN SİSTEM) ---
def download_via_cobalt(url, is_audio, download_dir):
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt-api.kwiatekm.tokyo/api/json",
        "https://api.wuk.sh/api/json"
    ]
    
    payload = {
        "url": url,
        "isAudioOnly": is_audio,
        "aFormat": "mp3" if is_audio else None,
        "vQuality": "720"
    }
    data = json.dumps(payload).encode('utf-8')
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    media_url = None
    title = "YouTube Media"

    for api in cobalt_instances:
        try:
            req = urllib.request.Request(api, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode())
                if res.get("status") in ["tunnel", "redirect"]:
                    media_url = res.get("url")
                    break
        except Exception:
            continue

    if not media_url:
        raise RuntimeError("Cobalt API fallback failed")

    ext = "mp3" if is_audio else "mp4"
    target_file = os.path.join(download_dir, f"media.{ext}")
    
    req_dl = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_dl, timeout=60) as resp, open(target_file, 'wb') as out_f:
        out_f.write(resp.read())

    return target_file, title

# --- INSTAGRAM, TIKTOK, FACEBOOK İÇİN YT-DLP MOTORU ---
def download_via_ytdlp(url, is_audio, download_dir):
    out_tmpl = os.path.join(download_dir, 'media.%(ext)s')
    ydl_opts = {
        'outtmpl': out_tmpl,
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    }

    if is_audio:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        ydl_opts.update({
            'format': 'best[ext=mp4][filesize<45M]/best[filesize<45M]/bestvideo[filesize<40M]+bestaudio/best',
            'merge_output_format': 'mp4',
        })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Video')
        files = os.listdir(download_dir)
        if not files:
            raise FileNotFoundError("Dosya indirilemedi.")
        return os.path.join(download_dir, files[0]), title

def smart_download(url, is_audio, download_dir):
    # Eğer YouTube linki ise doğrudan IP engelini aşan Cobalt motorunu çalıştır
    if "youtube.com" in url or "youtu.be" in url:
        try:
            return download_via_cobalt(url, is_audio, download_dir)
        except Exception:
            # Cobalt yanıt vermezse yedek olarak yt-dlp'yi dene
            return download_via_ytdlp(url, is_audio, download_dir)
    else:
        # Instagram, TikTok, Facebook için yt-dlp
        return download_via_ytdlp(url, is_audio, download_dir)

async def process_download(query, user_id, url, is_audio, context):
    try:
        await query.edit_message_text(get_text(user_id, 'downloading'))
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path, title = await asyncio.to_thread(smart_download, url, is_audio, tmp_dir)
            
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > 49.5:
                await query.edit_message_text(get_text(user_id, 'error_size'))
                return

            await query.edit_message_text(get_text(user_id, 'uploading'))

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
            
            await query.delete_message()
            
    except Exception as e:
        print(f"Hata detayi: {e}")
        await query.edit_message_text(get_text(user_id, 'error_general'))
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
        
        asyncio.create_task(process_download(query, user_id, url, data == "dl_audio", context))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    url_pattern = re.compile(r'https?://(?:www\.)?[^\s]+')
    match = url_pattern.search(text)
    
    if match:
        pending_links[user_id] = match.group(0)
        await update.message.reply_text(
            get_text(user_id, 'choose_format'),
            reply_markup=get_format_keyboard(user_id)
        )
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
