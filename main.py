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

def get_youtube_video_id(url: str):
    m = re.search(r'(?:v=|\/|shorts\/)([0-9A-Za-z_-]{11})', url)
    return m.group(1) if m else None

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

# =====================================================================
# 1. YOUTUBE MOTORU: INVIDIOUS PROXY AĞI (Render IP Engelini %100 Aşar)
# =====================================================================
def download_via_invidious(video_id: str, is_audio: bool, download_dir: str):
    # Dünya çapındaki aktif ve hızlı Invidious ağları
    instances = [
        "https://inv.nadeko.net",
        "https://yewtu.be",
        "https://invidious.nerdvpn.de",
        "https://iv.ggtyler.dev",
        "https://invidious.projectsegfau.lt"
    ]

    itag = "140" if is_audio else "18"  # 18 = 360p progressive MP4 (ses+görüntü tek parça)
    ext = "mp3" if is_audio else "mp4"

    for inst in instances:
        try:
            # 1. Video başlığını almayı dene
            title = "YouTube_Video"
            try:
                info_req = urllib.request.Request(
                    f"{inst}/api/v1/videos/{video_id}",
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                with urllib.request.urlopen(info_req, timeout=7) as resp:
                    info_data = json.loads(resp.read().decode('utf-8'))
                    title = info_data.get('title', 'YouTube_Video')
            except Exception:
                pass

            # 2. Invidious proxy tüneli üzerinden videoyu doğrudan indir
            stream_url = f"{inst}/latest_version?id={video_id}&itag={itag}&local=true"
            target_file = os.path.join(download_dir, f"media.{ext}")

            req = urllib.request.Request(stream_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=60) as resp:
                total_bytes = 0
                with open(target_file, 'wb') as out_f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > 49.5 * 1024 * 1024:
                            raise ValueError("FILE_TOO_LARGE")
                        out_f.write(chunk)

                # İndirilen dosya geçerli boyuttaysa dön
                if os.path.getsize(target_file) > 1024:
                    return target_file, title
        except ValueError as v_err:
            raise v_err
        except Exception:
            continue

    raise RuntimeError("Invidious proxy denemesi başarısız oldu.")

# =====================================================================
# 2. YOUTUBE MOTORU: COBALT API AĞI (2. Seviye Yedek)
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

                return target_file, os.path.splitext(filename)[0]
        except Exception:
            continue

    raise RuntimeError("Cobalt API başarısız.")

# =====================================================================
# 3. MOTOR: YT-DLP (Instagram, Facebook, TikTok + Yerel YouTube)
# =====================================================================
def download_via_ytdlp(url: str, is_audio: bool, download_dir: str):
    is_yt = is_youtube_url(url)
    opts = {
        'outtmpl': os.path.join(download_dir, 'media_%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 30,
        'retries': 5,
    }

    if is_yt:
        opts['format'] = 'bestaudio/best' if is_audio else 'best[ext=mp4]/18/22/best'
        opts['extractor_args'] = {'youtube': {'player_client': ['android_vr', 'web_embedded', 'mweb', 'ios']}}
    else:
        opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        opts['format'] = 'bestaudio/best' if is_audio else 'best[ext=mp4]/best'

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Media') if info else 'Media'
        final_file = get_downloaded_media_file(download_dir)
        if not final_file:
            raise RuntimeError("Medya dosyası bulunamadı.")
        return final_file, title

# --- ÇOKLU İNDİRME KÖPRÜSÜ ---
def download_media_sync(url: str, is_audio: bool, download_dir: str):
    # Instagram, Facebook ve TikTok -> Doğrudan yt-dlp ile sorunsuz indir
    if not is_youtube_url(url):
        return download_via_ytdlp(url, is_audio, download_dir)

    # YouTube: 1. Aşama -> Invidious Tüneli (Render IP engelini %100 baypas eder)
    vid = get_youtube_video_id(url)
    if vid:
        try:
            return download_via_invidious(vid, is_audio, download_dir)
        except ValueError as v_err:
            raise v_err
        except Exception as e1:
            print(f"Invidious hatasi: {e1}. Cobalt deneniyor...")

    # YouTube: 2. Aşama -> Cobalt CDN Tüneli
    try:
        return download_via_cobalt(url, is_audio, download_dir)
    except ValueError as v_err:
        raise v_err
    except Exception as e2:
        print(f"Cobalt hatasi: {e2}. yt-dlp deneniyor...")

    # YouTube: 3. Aşama -> yt-dlp (android_vr)
    return download_via_ytdlp(url, is_audio, download_dir)

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

        except ValueError as v:
            if str(v) == "FILE_TOO_LARGE":
                await safe_edit_text(status_msg, get_text(user_id, 'error_size', context))
            else:
                await safe_edit_text(status_msg, get_text(user_id, 'error_general', context))
        except Exception as e:
            print(f"Genel Hata [{url}]: {e}")
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
