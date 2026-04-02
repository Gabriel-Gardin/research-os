FROM python:3.12-slim

WORKDIR /app

# Dependências do sistema para pymupdf e OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev gcc \
    tesseract-ocr \
    tesseract-ocr-por \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "watcher.py"]