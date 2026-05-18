import ollama
import base64
import json
import re
import os
import time
from collections import Counter
from PIL import Image as PILImage
import io

INPUT_FOLDER = "documents/anonymized_docs"
OUTPUT_FILE = "documents/ocr_results/json/vlm_qwen25_results.json"

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)


PROMPT = """This is a Chadian government administrative document.
Extract the following fields exactly as written in the document.
Return ONLY a JSON object:

{
  "document_type": "the document type title as written e.g. ORDRE DE MISSION, ARRETE, DECISION, CORRESPONDANCE",
  "reference_number": "the reference number as written",
  "date": "the date as written",
  "person_names": ["full names of individuals mentioned"],
  "destination_or_subject": "destination city or subject of document",
  "organisation": "the ministry or organisation name",
  "signatory": "name of the person who signed",
  "budget_line": "budget imputation if present",
  "language": "fr or ar or bilingual",
  "quality_issues": ["list from: upside_down, flag_stripes, handwriting, faded_ink, security_background, typewriter"],
  "extraction_confidence": "high or medium or low"
}"""


def resize_for_vlm(img_path, max_size=1024):
    img = PILImage.open(img_path)
    img.thumbnail((max_size, max_size), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()

def extract_with_ollama(img_path):
    start = time.time()
    try:
        # with open(img_path, "rb") as f:
        #     img_data = base64.b64encode(f.read()).decode()

        img_data = resize_for_vlm(img_path)

        response = ollama.chat(
            model='qwen2.5vl:7b',
            messages=[{
                'role': 'user',
                'content': PROMPT,
                'images': [img_data]
            }]
        )

        duration = round(time.time() - start, 2)
        text = response['message']['content'].strip()

        # Clean markdown
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'^```\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group())
                except:
                    result = {"_raw": text, "_parse_error": True}
            else:
                result = {"_raw": text, "_parse_error": True}

        result["_filename"] = os.path.basename(img_path)
        result["_time"] = duration
        return result

    except Exception as e:
        return {
            "_filename": os.path.basename(img_path),
            "_time": round(time.time() - start, 2),
            "_error": str(e)
        }


# ── MAIN ──────────────────────────────────────────────────────────────────
images = [
    f for f in sorted(os.listdir(INPUT_FOLDER))
    if f.lower().endswith(('.png', '.jpg', '.jpeg'))
]

print(f"Found {len(images)} documents\n")
all_results = {}

for i, fname in enumerate(images):

# for i, fname in enumerate(images):
    img_path = os.path.join(INPUT_FOLDER, fname)
    print(f"[{i+1}/{len(images)}] {fname}")

    result = extract_with_ollama(img_path)
    all_results[fname] = result

    if result.get("_error"):
        print(f"  ERROR: {result['_error']}")
    elif result.get("_parse_error"):
        print(f"  PARSE ERROR: {result.get('_raw', '')[:150]}")
    else:
        print(f"  Type       : {result.get('document_type', '?')}")
        print(f"  Date       : {result.get('date', '?')}")
        print(f"  Confidence : {result.get('extraction_confidence', '?')}")
        issues_list = [i for i in (result.get('quality_issues') or []) if i]
        print(f"  Issues     : {', '.join(issues_list) or 'none'}")
    print(f"  Time       : {result.get('_time', 0)}s")
    print()

    # Save after each document
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

print(f"Done. Results saved to {OUTPUT_FILE}")

# Summary
print("\n=== SUMMARY ===")
valid = [r for r in all_results.values() if not r.get('_error') and not r.get('_parse_error')]
print(f"Successfully extracted: {len(valid)}/{len(all_results)}")

confs = [r.get('extraction_confidence', 'unknown') for r in valid]
for level in ['high', 'medium', 'low']:
    count = confs.count(level)
    pct = round(count / len(valid) * 100) if valid else 0
    print(f"  {level.capitalize():8}: {count}/{len(valid)} ({pct}%)")

issues = []
for r in valid:
    issues.extend([i for i in (r.get('quality_issues') or []) if i])

if issues:
    print("\nQuality issues:")
    for issue, count in Counter(issues).most_common():
        print(f"  {issue}: {count} docs")

avg_time = sum(r.get('_time', 0) for r in all_results.values()) / len(all_results) if all_results else 0
print(f"\nAverage time: {avg_time:.1f}s per document")
print(f"Estimated for 50,000 docs: {50000 * avg_time / 3600:.1f} hours")