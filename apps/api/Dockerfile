FROM python:3.11-slim

WORKDIR /app

# System packages: poppler for pdf2image, OpenCV runtime libs, Tesseract + language packs,
# ocrmypdf (image originals converted to PDF/A before an electronic signature)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    tesseract-ocr-ara \
    ocrmypdf \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first so this layer is cached on code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync/ ./sync/
COPY scripts/ ./scripts/
COPY api_server.py auth.py audit.py cache.py i18n.py rate_limit.py ingest_router.py documents_router.py review_router.py admin_router.py versions_router.py classification_router.py history.py page_text.py export_format.py document_files.py share_router.py export_router.py lifecycle.py lifecycle_router.py retention.py destruction.py pdf_report.py notifications.py notifications_router.py locks.py relocate.py checkout_router.py comments_router.py signatures.py signature_router.py users_router.py create_admin.py create_tenant.py create_api_key.py create_prompt_preset.py ./

# documents/ is mounted as a volume at runtime - never baked into the image
VOLUME ["/app/documents"]

EXPOSE 8000

# PROJECT_ROOT=/app tells api_server.py that documents/ lives at /app/documents
ENV PROJECT_ROOT=/app

CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8000"]
