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

# --- RENDER WEB SERVICE HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return  # Log kirliliğini engelle

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- ÇOK DİLLİ METİNLER (UZ, RU, EN, TR) ---
TEXTS = {
    'uz': {
        'welcome': "Assalomu alaykum! Video yuklab beruvchi botga xush kelibsiz.\n\nYouTube, Instagram, TikTok yoki Facebook havolasini yuboring.",
        'choose_format': "Qaysi formatda yuklab olmoqchisiz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ovoz (MP3)",
        'downloading': "Yuklab olinmoqda, iltimos kuting...",
        'uploading': "Telegramga yuklanmoqda...",
        'error_size': "Kechirasiz, video hajmi Telegram cheklovidan (50 MB) katta.",
        'error_general': "Yuklab olishda xatolik yuz berdi. Havola to'g'riligini tekshiring."
    },
    'ru': {
        'welcome': "Здравствуйте! Добро пожаловать в загрузчик видео.\n\nОтправьте ссылку из YouTube, Instagram, TikTok или Facebook.",
        'choose_format': "В каком формате хотите скачать?",
        'video_btn': "🎬 Видео",
        'audio_btn': "🎵 Аудио (MP3)",
        'downloading': "Скачивается, пожалуйста подождите...",
        'uploading': "Отправка в Telegram...",
        'error_size': "К сожалению, размер файла превышает лимит Telegram (50 МБ).",
        'error_general': "Произошла ошибка при загрузке. Проверьте правильность ссылки."
    },
    'en': {
        'welcome': "Hello! Welcome to the Video Downloader bot.\n\nSend a link from YouTube, Instagram, TikTok, or Facebook.",
        'choose_format': "Choose download format:",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Audio (MP3)",
        'downloading': "Downloading, please wait...",
        'uploading': "Uploading to Telegram...",
        'error_size': "Sorry, the media exceeds Telegram's 50 MB limit.",
        'error_general': "An error occurred while downloading. Please check the link."
    },
    'tr': {
        'welcome': "Merhaba! Video İndirme Botuna hoş geldiniz.\n\nYouTube, Instagram, TikTok veya Facebook linki gönderebilirsiniz.",
        'choose_format': "Hangi formatta indirmek istersiniz?",
        'video_btn': "🎬 Video",
        'audio_btn': "🎵 Ses (MP3)",
        'downloading': "İndiriliyor, lütfen bekleyin...",
        'uploading': "Telegram'a yükleniyor...",
        'error_size': "Üzgünüz, dosya Telegram'ın 50 MB sınırından daha büyük.",
        'error_general': "İndirme sırasında bir hata oluştu. Linkin geçerli olduğundan emin olun."
    }
}

user_languages = {}
pending_links = {}

def get_text(user_id, key):
    lang = user_languages.get(user_id, 'en')
    return TEXTS.get(lang, TEXTS['en']).get(key, '')

def get_language_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")
        ],
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_format_keyboard(user_id):
    keyboard = [
        [
            InlineKeyboardButton(get_text(user_id, 'video_btn'), callback_data="dl_video"),
            InlineKeyboardButton(get_text(user_id, 'audio_btn'), callback_data="dl_audio")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "Iltimos, tilni tanlang / Выберите язык / Select language / Lütfen dil seçin:",
        reply_markup=get_language_keyboard()
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("lang_"):
        lang = data.split("_")[1]
        user_languages[user_id] = lang
        await query.edit_message_text(
            f"✅ {get_text(user_id, 'welcome')}"
        )
        return

    if data in ["dl_video", "dl_audio"]:
        url = pending_links.get(user_id)
        if not url:
            await query.edit_message_text(get_text(user_id, 'error_general'))
            return

        is_audio = (data == "dl_audio")
        await query.edit_message_text(get_text(user_id, 'downloading'))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, process_and_send_media, query, user_id, url, is_audio, context)

def download_media(url, is_audio, download_dir):
    out_tmpl = os.path.join(download_dir, '%(id)s.%(ext)s')
    if is_audio:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': out_tmpl,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
        }
    else:
        ydl_opts = {
            'format': 'bestvideo[filesize<45M]+bestaudio/best[filesize<45M]/best[filesize<45M]/best',
            'outtmpl': out_tmpl,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if is_audio:
            filename = os.path.splitext(filename)[0] + '.mp3'
        elif not filename.endswith('.mp4'):
            filename = os.path.splitext(filename)[0] + '.mp4'
        return filename, info.get('title', 'Media')

def process_and_send_media(query, user_id, url, is_audio, context):
    async def run_async_steps():
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                loop = asyncio.get_running_loop()
                file_path, title = await loop.run_in_executor(None, download_media, url, is_audio, tmp_dir)
                
                if not os.path.exists(file_path):
                    files = os.listdir(tmp_dir)
                    if files:
                        file_path = os.path.join(tmp_dir, files[0])
                    else:
                        raise FileNotFoundError("Dosya bulunamadı.")

                size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if size_mb > 49.5:
                    await context.bot.send_message(chat_id=user_id, text=get_text(user_id, 'error_size'))
                    return

                await query.edit_message_text(get_text(user_id, 'uploading'))
                
                with open(file_path, 'rb') as f:
                    if is_audio:
                        await context.bot.send_audio(chat_id=user_id, audio=f, title=title)
                    else:
                        await context.bot.send_video(chat_id=user_id, video=f, caption=title, supports_streaming=True)
                
                await query.delete_message()
            except Exception as e:
                print(f"Hata: {e}")
                await context.bot.send_message(chat_id=user_id, text=get_text(user_id, 'error_general'))
            finally:
                pending_links.pop(user_id, None)

    asyncio.run_coroutine_threadsafe(run_async_steps(), context.application.loop)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    url_pattern = re.compile(r'https?://(?:www\.)?[^\s]+')
    match = url_pattern.search(text)
    
    if match:
        url = match.group(0)
        pending_links[user_id] = url
        await update.message.reply_text(
            get_text(user_id, 'choose_format'),
            reply_markup=get_format_keyboard(user_id)
        )
    else:
        if user_id not in user_languages:
            await update.message.reply_text(
                "Iltimos, tilni tanlang / Select language:",
                reply_markup=get_language_keyboard()
            )
        else:
            await update.message.reply_text(get_text(user_id, 'welcome'))

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN ayarlanmadi!")

    # Render'ın servisi canlı tutması için web portunu aç
    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot basariyla calisiyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
