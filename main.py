import os
import re
import json
import uuid
import shutil
import asyncio
import tempfile
import threading
import urllib.request
import urllib.error
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

try:
    from pytubefix import YouTube as PytubeFix
except ImportError:
    PytubeFix = None

# --- RENDER 7/24 SAĞLIK KONTROLÜ (PORT BINDING) ---
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
        'welcome': "Assalomu alaykum! Video yuklovchi botga xush kelibsiz.\n\nYouTube, Instagram, TikTok yoki Facebook havolasini yuboring.",
        'choose_format': "Qaysi formatda yuklab olmoqchisiz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ovoz (MP3)",
        'downloading': "⏳ Video yuklab olinmoqda, iltimos kuting...",
        'uploading': "📤 Telegramga yuklanmoqda...",
        'error_size': "⚠️ Fayl hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "❌ Yuklab olishda xatolik yuz berdi. Havolani tekshiring.",
    },
    'ru': {
        'welcome': "Здравствуйте! Отправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "Выберите формат загрузки:",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио (MP3)",
        'downloading': "⏳ Скачивается, пожалуйста подождите...",
        'uploading': "📤 Отправка в Telegram...",
        'error_size': "⚠️ Размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "❌ Ошибка при скачивании. Проверьте ссылку.",
    },
    'en': {
        'welcome': "Hello! Send a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose download format:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio (MP3)",
        'downloading': "⏳ Downloading media, please wait...",
        'uploading': "📤 Uploading to Telegram...",
        'error_size': "⚠️ File exceeds Telegram's 50 MB limit.",
        'error_general': "❌ Download failed. Please verify the link.",
    },
    'tr': {
        'welcome': "Merhaba! YouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "Hangi formatta indirmek istersiniz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ses (MP3)",
        'downloading': "⏳ Medya indiriliyor, lütfen bekleyin...",
        'uploading': "📤 Telegram'a yükleniyor...",
        'error_size': "⚠️ Dosya boyutu Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "❌ İndirme başarısız oldu. Linki kontrol edin veya videonun herkese açık olduğundan emin olun.",
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

# --- 11 KB'LIK SAHTE DOSYALARI ENGELLEYEN DOĞRULAYICI ---
def is_valid_media_file(filepath: str) -> bool:
    if not filepath or not os.path.exists(filepath):
        return False
    size = os.path.getsize(filepath)
    # 200 KB'dan küçük bir YouTube videosu olamaz; bunlar kesinlikle HTML hata sayfasıdır
    if size < 200 * 1024:
        return False
    try:
        with open(filepath, 'rb') as f:
            header = f.read(64)
            # Eğer HTML veya JSON hata metni içeriyorsa sahtedir
            if header.startswith((b'<!DOCTYPE', b'<!doctype', b'<html', b'{"error', b'{"status', b'Error', b'Access Denied')):
                return False
        return True
    except Exception:
        return False

def get_downloaded_media_file(directory: str):
    valid_files = []
    for f in os.listdir(directory):
        full_path = os.path.join(directory, f)
        if os.path.isfile(full_path):
            if not f.endswith(('.part', '.ytdl', '.temp', '.aria2', '.json', '.jpg', '.jpeg', '.png', '.webp', '.vtt', '.srt')):
                if is_valid_media_file(full_path):
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

# =====================================================================
# 1. MOTOR: PYTUBEFIX PROGRESSIVE (Ses ve Görüntü Birleşik - FFmpeg İstemez)
# =====================================================================
def download_via_pytubefix(url: str, is_audio: bool, download_dir: str):
    if not PytubeFix:
        raise ImportError("pytubefix yüklü değil.")

    # Android istemcisi veri merkezi IP kısıtlamasına takılmaz
    for client in ['ANDROID', 'MWEB']:
        try:
            yt = PytubeFix(url, client=client)
            title = yt.title or "YouTube_Media"

            if is_audio:
                stream = yt.streams.get_audio_only()
                if not stream:
                    stream = yt.streams.filter(only_audio=True).first()
            else:
                stream = yt.streams.filter(progressive=True).order_by('resolution').desc().first()
                if not stream:
                    stream = yt.streams.get_highest_resolution()

            if stream:
                target_file = stream.download(output_path=download_dir)
                if is_valid_media_file(target_file):
                    return target_file, title
        except Exception:
            continue

    raise RuntimeError("Pytubefix indirilemedi.")

# =====================================================================
# 2. MOTOR: YT-DLP (android_vr & mweb İstemcisi ile Render Uyumlu)
# =====================================================================
def download_via_ytdlp(url: str, is_audio: bool, download_dir: str):
    is_yt = is_youtube_url(url)
    has_ffmpeg = shutil.which('ffmpeg') is not None

    opts = {
        'outtmpl': os.path.join(download_dir, 'media_%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 30,
        'retries': 5,
    }

    if is_yt:
        # android_vr ve mweb, Google'ın sunucu IP bloklarını aşan istemcilerdir
        opts['extractor_args'] = {
            'youtube': {
                'player_client': ['android_vr', 'mweb', 'ios', 'web_embedded'],
            }
        }
        if has_ffmpeg:
            opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best' if is_audio else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            if not is_audio:
                opts['merge_output_format'] = 'mp4'
        else:
            opts['format'] = 'bestaudio/best' if is_audio else 'best[ext=mp4]/18/22/best'
    else:
        # Instagram, Facebook, TikTok
        opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        opts['format'] = 'bestaudio/best' if is_audio else 'best[ext=mp4]/best'

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Media') if info else 'Media'
        final_file = get_downloaded_media_file(download_dir)
        if final_file and is_valid_media_file(final_file):
            return final_file, title
        raise RuntimeError("yt-dlp geçerli medya indiremedi.")

# =====================================================================
# 3. MOTOR: COBALT API AĞI (Doğrulanmış Harici Akış)
# =====================================================================
def download_via_cobalt(url: str, is_audio: bool, download_dir: str):
    instances = ["https://cobalt-api.ayo.tf", "https://cobalt.canine.tools", "https://api.cobalt.tools"]
    payload = json.dumps({
        "url": url,
        "videoQuality": "720",
        "downloadMode": "audio" if is_audio else "auto",
        "audioFormat": "mp3" if is_audio else None
    }).encode('utf-8')
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}

    for inst in instances:
        try:
            req = urllib.request.Request(f"{inst}/", data=payload, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=12) as resp:
                res = json.loads(resp.read().decode('utf-8'))

            download_url = res.get("url")
            if not download_url and res.get("picker"):
                download_url = res.get("picker")[0].get("url")

            if download_url:
                filename = res.get("filename", "YouTube_Media.mp4")
                target_file = os.path.join(download_dir, filename)

                dl_req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(dl_req, timeout=60) as stream_resp:
                    with open(target_file, 'wb') as out_f:
                        while True:
                            chunk = stream_resp.read(64 * 1024)
                            if not chunk:
                                break
                            out_f.write(chunk)

                if is_valid_media_file(target_file):
                    return target_file, os.path.splitext(filename)[0]
        except Exception:
            continue

    raise RuntimeError("Cobalt API başarısız.")

# --- KOMBİNE İNDİRME KÖPRÜSÜ ---
def download_media_sync(url: str, is_audio: bool, download_dir: str):
    # Instagram, TikTok, Facebook -> Doğrudan yt-dlp ile sorunsuz çalışır
    if not is_youtube_url(url):
        return download_via_ytdlp(url, is_audio, download_dir)

    # YouTube: 1. Aşama -> Pytubefix Progressive (Kendi kendine yeten tek parça MP4)
    try:
        return download_via_pytubefix(url, is_audio, download_dir)
    except Exception as e1:
        print(f"Pytubefix hatası: {e1}. yt-dlp deneniyor...")

    # YouTube: 2. Aşama -> yt-dlp (android_vr VR başlığı taklidi)
    try:
        return download_via_ytdlp(url, is_audio, download_dir)
    except Exception as e2:
        print(f"yt-dlp hatası: {e2}. Cobalt deneniyor...")

    # YouTube: 3. Aşama -> Cobalt API
    return download_via_cobalt(url, is_audio, download_dir)

# --- İŞLEME VE GÖNDERME ---
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
        raise ValueError("BOT_TOKEN ortam değişkeni eksik!")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot 7/24 kesintisiz çalışıyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
