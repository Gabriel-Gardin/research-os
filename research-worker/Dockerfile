FROM python:3.12-slim

WORKDIR /app

# Dependências do sistema para pymupdf, OCR e MinerU (opencv)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev gcc \
    libgl1 libglib2.0-0 \
    tesseract-ocr \
    tesseract-ocr-por \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modelos do MinerU (~2 GB) ficam na imagem para o parse rodar 100% local
RUN mineru-kit models download --tier standard

COPY . .

CMD ["python", "-u", "watcher.py"]