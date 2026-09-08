import os
import re
import asyncio
import tempfile
import threading
import json
import subprocess
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

# --- RENDER 7/24 SAĞLIK SUNUCUSU ---
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
        'downloading': "⏳ Yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Video hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Yuklab olishda xatolik yuz berdi. Havolani tekshiring."
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать в загрузчик видео.\n\nОтправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "Выберите формат для YouTube:",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио (MP3)",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Ошибка загрузки. Проверьте ссылку."
    },
    'en': {
        'welcome': "Hello! Welcome to Video Downloader bot.\n\nSend a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose format for YouTube:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio (MP3)",
        'downloading': "⏳ Downloading, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ Download failed. Please verify the link."
    },
    'tr': {
        'welcome': "Merhaba! Video İndirme Botuna hoş geldiniz.\n\nYouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "YouTube için format seçin:",
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

def extract_youtube_id(url):
    match = re.search(r'(?:v=|\/|shorts\/)([0-9A-Za-z_-]{11})', url)
    return match.group(1) if match else None

# --- YOUTUBE ÖZEL DAĞITIK AKIŞ MOTORU (DATACENTER IP ENGELİNİ AŞAR) ---
def download_youtube_advanced(url, is_audio, download_dir):
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Gecersiz YouTube linki.")

    # 1. Piped API Dağıtık Ağı
    piped_instances = [
        "https://api.piped.privacydev.net",
        "https://pipedapi.tokhmi.xyz",
        "https://pipedapi.ducks.party",
        "https://pipedapi.drgns.space",
        "https://pipedapi.kavin.rocks"
    ]

    title = "YouTube Media"
    stream_url = None

    for inst in piped_instances:
        try:
            req = urllib.request.Request(
                f"{inst}/streams/{video_id}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                title = data.get("title", "YouTube Media")
                
                if is_audio:
                    audio_streams = data.get("audioStreams", [])
                    if audio_streams:
                        stream_url = audio_streams[0].get("url")
                        break
                else:
                    video_streams = data.get("videoStreams", [])
                    # Hem ses hem görüntü içeren birleşik akışları ara
                    combined = [s for s in video_streams if not s.get("videoOnly", True)]
                    if combined:
                        stream_url = combined[0].get("url")
                        break
                    elif video_streams:
                        stream_url = video_streams[0].get("url")
                        break
        except Exception:
            continue

    # 2. Invidious API Dağıtık Ağı (Piped yanıt vermezse)
    if not stream_url:
        invidious_instances = [
            "https://invidious.nerdvpn.de",
            "https://inv.nadeko.net",
            "https://invidious.jing.rocks",
            "https://inv.tux.pizza"
        ]
        for inst in invidious_instances:
            try:
                req = urllib.request.Request(
                    f"{inst}/api/v1/videos/{video_id}",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    title = data.get("title", "YouTube Media")
                    if is_audio:
                        adaptive = data.get("adaptiveFormats", [])
                        audios = [a for a in adaptive if "audio" in a.get("type", "")]
                        if audios:
                            stream_url = audios[0].get("url")
                            break
                    else:
                        formats = data.get("formatStreams", [])
                        if formats:
                            stream_url = formats[0].get("url")
                            break
            except Exception:
                continue

    if not stream_url:
        raise RuntimeError("YouTube akis adresi alinamadi.")

    # Akışı indir
    raw_file = os.path.join(download_dir, "raw_stream")
    req_dl = urllib.request.Request(stream_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_dl, timeout=90) as resp, open(raw_file, 'wb') as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    # FFmpeg ile Telegram'a uygun formata çevir
    if is_audio:
        final_file = os.path.join(download_dir, "audio.mp3")
        subprocess.run(
            ['ffmpeg', '-y', '-i', raw_file, '-vn', '-b:a', '192k', final_file],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return final_file, title
    else:
        final_file = os.path.join(download_dir, "video.mp4")
        subprocess.run(
            ['ffmpeg', '-y', '-i', raw_file, '-c', 'copy', '-movflags', '+faststart', final_file],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return final_file, title

# --- INSTAGRAM, TIKTOK, FACEBOOK MOTORU ---
def download_social_media(url, download_dir):
    out_tmpl = os.path.join(download_dir, 'video.%(ext)s')
    ydl_opts = {
        'outtmpl': out_tmpl,
        'format': 'best[ext=mp4][filesize<45M]/best[filesize<45M]/best',
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Video')
        files = os.listdir(download_dir)
        if not files:
            raise FileNotFoundError("Video indirilemedi.")
        return os.path.join(download_dir, files[0]), title

def universal_download(url, is_audio, download_dir):
    if is_youtube_url(url):
        return download_youtube_advanced(url, is_audio, download_dir)
    else:
        return download_social_media(url, download_dir)

async def process_download(status_msg, user_id, url, is_audio, context):
    try:
        await status_msg.edit_text(get_text(user_id, 'downloading'))
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path, title = await asyncio.to_thread(universal_download, url, is_audio, tmp_dir)
            
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

    # YouTube: Seçenek butonları çıkar (Video / Ses)
    if is_youtube_url(url):
        pending_links[user_id] = url
        await update.message.reply_text(
            get_text(user_id, 'choose_format'),
            reply_markup=get_yt_format_keyboard(user_id)
        )
    # Instagram, TikTok, Facebook: Sormadan direkt video indir
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
