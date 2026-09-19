FROM python:3.11-slim

WORKDIR /app

# System packages: poppler for pdf2image, OpenCV runtime libs, Tesseract + language packs
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        tesseract-ocr \
        tesseract-ocr-fra \
        tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first so this layer is cached on code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api_server.py auth.py audit.py cache.py i18n.py rate_limit.py ingest_router.py documents_router.py review_router.py admin_router.py create_admin.py create_tenant.py ./

# documents/ is mounted as a volume at runtime — never baked into the image
VOLUME ["/app/documents"]

EXPOSE 8000

# PROJECT_ROOT=/app tells api_server.py that documents/ lives at /app/documents
ENV PROJECT_ROOT=/app

CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8000"]
