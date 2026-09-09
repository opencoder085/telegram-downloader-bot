FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    tesseract-ocr \
    tesseract-ocr-ara \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    tesseract-ocr-tur \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    tesseract-ocr-uzb \
    tesseract-ocr-uzb-cyrl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "python bot.py 2>/dev/null || python main.py 2>/dev/null || python app.py"]
