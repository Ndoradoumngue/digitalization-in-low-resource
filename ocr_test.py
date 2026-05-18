import pytesseract
import easyocr
from surya.model.detection.model import load_model as load_det_model, load_processor as load_det_processor
from surya.model.recognition.model import load_model as load_rec_model
from surya.model.recognition.processor import load_processor as load_rec_processor
from surya.ocr import run_ocr
import cv2
import os
import json
import time
from PIL import Image
import numpy as np


# ── CONFIG ────────────────────────────────────────────────────────────────
INPUT_FOLDER      = "documents/anonymized_docs"
OUTPUT_FOLDER     = "documents/ocr_results"
JSON_FOLDER       = "documents/ocr_results/json"
PREPROCESS_FOLDER = "documents/ocr_results/preprocessed"

os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(JSON_FOLDER, exist_ok=True)
os.makedirs(PREPROCESS_FOLDER, exist_ok=True)



# ── INIT MODELS (once) ────────────────────────────────────────────────────
print("Loading EasyOCR...")
easy_reader_latin  = easyocr.Reader(['fr', 'en'], gpu=False)
easy_reader_arabic = easyocr.Reader(['ar', 'en'], gpu=False)

print("Loading Surya...")
det_processor = load_det_processor()
det_model     = load_det_model()
rec_model     = load_rec_model()
rec_processor = load_rec_processor()

print("Models loaded. Starting tests...\n")

# ── HELPERS ───────────────────────────────────────────────────────────────


def preprocess(img_path):
    """Basic preprocessing — grayscale, denoise, threshold"""
    img       = cv2.imread(img_path)
    gray      = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised  = cv2.fastNlMeansDenoising(gray, h=10)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out_path  = os.path.join(
        PREPROCESS_FOLDER,
        os.path.basename(img_path).replace(".png", "_preprocessed.png").replace(".jpg", "_preprocessed.jpg")
    )
    cv2.imwrite(out_path, binary)
    return img_path, out_path

def run_tesseract(img_path, lang="fra+ara"):
    """Run Tesseract with French + Arabic — with confidence scores"""
    try:
        start = time.time()
        data = pytesseract.image_to_data(
            Image.open(img_path), lang=lang,
            output_type=pytesseract.Output.DICT
        )
        # Filter out empty text and -1 confidence values
        words = [(w, c) for w, c in zip(data['text'], data['conf'])
                 if w.strip() and c != -1]
        text       = " ".join([w for w, c in words])
        confidence = round(sum([c for w, c in words]) / len(words) / 100, 3) if words else 0
        return {
            "text":       text.strip(),
            "confidence": confidence,
            "time":       round(time.time() - start, 2),
            "error":      None
        }
    except Exception as e:
        return {"text": "", "confidence": 0, "time": 0, "error": str(e)}

def run_easyocr(img_path):
    """Run EasyOCR with separate Latin and Arabic readers"""
    try:
        start          = time.time()
        results_latin  = easy_reader_latin.readtext(img_path)
        results_arabic = easy_reader_arabic.readtext(img_path)
        combined       = sorted(results_latin + results_arabic, key=lambda x: x[0][0][1])
        text           = "\n".join([r[1] for r in combined])
        confidence     = round(sum([r[2] for r in combined]) / len(combined), 3) if combined else 0
        return {"text": text.strip(), "confidence": confidence, "time": round(time.time() - start, 2), "error": None}
    except Exception as e:
        return {"text": "", "confidence": 0, "time": 0, "error": str(e)}


def run_surya(img_path):
    """Run Surya OCR with French and Arabic"""
    try:
        start       = time.time()
        image       = Image.open(img_path).convert("RGB")
        predictions = run_ocr(
            [image], [["fr", "ar"]],
            det_model, det_processor,
            rec_model, rec_processor
        )
        lines       = [line.text       for line in predictions[0].text_lines]
        confidences = [line.confidence for line in predictions[0].text_lines]
        text        = "\n".join(lines)
        confidence  = round(sum(confidences) / len(confidences), 3) if confidences else 0
        return {"text": text.strip(), "confidence": confidence, "time": round(time.time() - start, 2), "error": None}
    except Exception as e:
        return {"text": "", "confidence": 0, "time": 0, "error": str(e)}


# ── MAIN LOOP ─────────────────────────────────────────────────────────────
images = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]

print(f"Found {len(images)} documents to test\n")
all_results = {}

# for fname in sorted(images):
#     print(f"Processing: {fname}")
#     img_path = os.path.join(INPUT_FOLDER, fname)

#     print("  Running Tesseract...")
#     tess_raw  = run_tesseract(img_path)

#     print("  Running EasyOCR...")
#     easy_raw  = run_easyocr(img_path)

#     print("  Running Surya...")
#     surya_raw = run_surya(img_path)

#     result = {
#         "filename":  fname,
#         "tesseract": tess_raw,
#         "easyocr":   easy_raw,
#         "surya":     surya_raw,
#     }
#     all_results[fname] = result

#     out_path = os.path.join(OUTPUT_FOLDER, fname.replace(".png", "_results.json").replace(".jpg", "_results.json"))
#     with open(out_path, "w", encoding="utf-8") as f:
#         json.dump(result, f, ensure_ascii=False, indent=2)

#     print(f"  Tesseract : {len(tess_raw['text'])} chars  |  {tess_raw['time']}s")
#     print(f"  EasyOCR   : {len(easy_raw['text'])} chars  |  {easy_raw['time']}s  |  conf: {easy_raw.get('confidence', 'N/A')}")
#     print(f"  Surya     : {len(surya_raw['text'])} chars  |  {surya_raw['time']}s  |  conf: {surya_raw.get('confidence', 'N/A')}")
#     print()




for fname in sorted(images):
    print(f"Processing: {fname}")
    img_path = os.path.join(INPUT_FOLDER, fname)

    # ── PREPROCESSING ──────────────────────────────────────────────────
    print("  Preprocessing...")
    _, preprocessed_path = preprocess(img_path)

    # ── RAW IMAGE — all three tools ────────────────────────────────────
    print("  Running Tesseract (raw)...")
    tess_raw = run_tesseract(img_path)

    print("  Running EasyOCR (raw)...")
    easy_raw = run_easyocr(img_path)

    print("  Running Surya (raw)...")
    surya_raw = run_surya(img_path)

    # ── PREPROCESSED IMAGE — all three tools ───────────────────────────
    print("  Running Tesseract (preprocessed)...")
    tess_pre = run_tesseract(preprocessed_path)

    print("  Running EasyOCR (preprocessed)...")
    easy_pre = run_easyocr(preprocessed_path)

    print("  Running Surya (preprocessed)...")
    surya_pre = run_surya(preprocessed_path)

    # ── SAVE RESULTS ───────────────────────────────────────────────────
    result = {
        "filename": fname,
        "raw": {
            "tesseract": tess_raw,
            "easyocr":   easy_raw,
            "surya":     surya_raw,
        },
        "preprocessed": {
            "tesseract": tess_pre,
            "easyocr":   easy_pre,
            "surya":     surya_pre,
        }
    }

    all_results[fname] = result

    out_path = os.path.join(
        JSON_FOLDER,
        fname.replace(".png", "_results.json").replace(".jpg", "_results.json")
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ── PRINT SUMMARY ──────────────────────────────────────────────────
    print(f"  {'Tool':<12} {'Raw chars':>10} {'Raw conf':>9} {'Pre chars':>10} {'Pre conf':>9} {'Change':>8}")
    print(f"  {'-'*60}")
    for tool, raw, pre in [
        ('Tesseract', tess_raw, tess_pre),
        ('EasyOCR',   easy_raw, easy_pre),
        ('Surya',     surya_raw, surya_pre),
    ]:
        r_conf = raw.get('confidence', 0) or 0
        p_conf = pre.get('confidence', 0) or 0
        change = f"+{(p_conf - r_conf):.3f}" if p_conf >= r_conf else f"{(p_conf - r_conf):.3f}"
        print(f"  {tool:<12} {len(raw['text']):>10} {r_conf:>9.3f} {len(pre['text']):>10} {p_conf:>9.3f} {change:>8}")
    print()


combined_path = os.path.join(JSON_FOLDER, "_all_results.json")
with open(combined_path, "w", encoding="utf-8") as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)

print(f"Done. All results saved.")
print(f"JSON files    : {JSON_FOLDER}/")
print(f"Preprocessed  : {PREPROCESS_FOLDER}/")
print(f"Combined file : {combined_path}")


