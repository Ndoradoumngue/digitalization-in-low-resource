from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
import torch
import json
import re
import os
import time
from PIL import Image
from collections import Counter

# ── CONFIG ────────────────────────────────────────────────────────────────
INPUT_FOLDER = "documents/anonymized_docs"
OUTPUT_FILE  = "documents/ocr_results/json/vlm_local_results.json"
os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

# ── DEVICE ────────────────────────────────────────────────────────────────
# device = "mps" if torch.backends.mps.is_available() else "cpu"
device = "CPU"
print(f"Using device: {device}")

# ── LOAD MODEL ────────────────────────────────────────────────────────────
MODEL_ID = "Qwen/Qwen2-VL-2B-Instruct" # Qwen/Qwen2-VL-2B-Instruct 2B parameter model — runs on CPU with 8GB RAM For more accurate, needs 16GB RAM and use model "Qwen/Qwen2-VL-7B-Instruct"
print(f"Loading {MODEL_ID}...")
print("First run downloads ~15GB. Subsequent runs load from cache.\n")

model = Qwen2VLForConditionalGeneration.from_pretrained(
    MODEL_ID,
    torch_dtype=torch.float32, # float16 works on MPS, saves memory. float32 for CPU
    device_map="cpu" # device
)

processor = AutoProcessor.from_pretrained(MODEL_ID)
print("Model loaded.\n")

# ── PROMPT ────────────────────────────────────────────────────────────────
PROMPT = """This is a Chadian government administrative document in French and/or Arabic.

Extract all available fields and return ONLY a valid JSON object.
Use null for any field that is absent or illegible:

{
  "document_type": "type of document e.g. ordre_de_mission, arrete, correspondance, decision, facture, certificat",
  "reference_number": "reference number",
  "date": "document date in YYYY-MM-DD if possible, otherwise as written",
  "person_names": ["list of all named individuals"],
  "functions": ["their job titles or functions"],
  "destination_or_subject": "destination city or subject matter",
  "organisation": "issuing ministry or organisation",
  "signatory": "name of signatory",
  "budget_line": "budget imputation or company charged if present",
  "language": "fr or ar or bilingual",
  "bilingual_layout": "parallel_columns or sequential or french_only or arabic_only",
  "quality_issues": ["list any observed: flag_stripes, handwriting_annotations, upside_down, faded_ink, security_background, physical_damage, typewriter, layered_documents"],
  "extraction_confidence": "high or medium or low"
}

Return ONLY the JSON. No explanation. No markdown fences."""

# ── EXTRACTION FUNCTION ───────────────────────────────────────────────────
def extract_with_qwen(img_path):
    start = time.time()
    try:
        image = Image.open(img_path).convert("RGB")

        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text",  "text": PROMPT}
            ]
        }]

        text = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt"
        )

        # Move to device
        # inputs = {k: v.to(device) for k, v in inputs.items()}

        # Generate
        with torch.no_grad():
            generated_ids = model.generate(
                **inputs,
                max_new_tokens=512,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None
            )

        generated_ids_trimmed = [
            out_ids[len(in_ids):]
            for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        response = processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False
        )[0].strip()

        duration = round(time.time() - start, 2)

        # Clean markdown fences if present
        response = re.sub(r'^```json\s*', '', response)
        response = re.sub(r'^```\s*',     '', response)
        response = re.sub(r'\s*```$',     '', response)

        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                except:
                    result = {"_raw": response, "_parse_error": True}
            else:
                result = {"_raw": response, "_parse_error": True}

        result["_filename"] = os.path.basename(img_path)
        result["_time"]     = duration
        return result

    except Exception as e:
        return {
            "_filename": os.path.basename(img_path),
            "_time":     round(time.time() - start, 2),
            "_error":    str(e)
        }

# ── MAIN LOOP ─────────────────────────────────────────────────────────────
images = [
    f for f in sorted(os.listdir(INPUT_FOLDER))
    if f.lower().endswith(('.png', '.jpg', '.jpeg'))
]

print(f"Found {len(images)} documents\n")
all_results = {}

for i, fname in enumerate(images):
    img_path = os.path.join(INPUT_FOLDER, fname)
    print(f"[{i+1}/{len(images)}] {fname}")

    result = extract_with_qwen(img_path)
    all_results[fname] = result

    if result.get("_error"):
        print(f"  ERROR: {result['_error']}")
    elif result.get("_parse_error"):
        print(f"  PARSE ERROR — raw response:")
        print(f"  {result.get('_raw', '')[:200]}")
    else:
        print(f"  Type       : {result.get('document_type', 'unknown')}")
        print(f"  Date       : {result.get('date', 'unknown')}")
        print(f"  Confidence : {result.get('extraction_confidence', 'unknown')}")
        print(f"  Language   : {result.get('language', 'unknown')}")
        print(f"  Issues     : {', '.join(result.get('quality_issues', [])) or 'none'}")
    print(f"  Time       : {result.get('_time', 0)}s")
    print()

    # Save after each document in case of interruption
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

print(f"\nDone. All results saved to {OUTPUT_FILE}")

# ── SUMMARY ───────────────────────────────────────────────────────────────
print("\n=== SUMMARY ===")
valid  = [r for r in all_results.values() if not r.get('_error') and not r.get('_parse_error')]
errors = [r for r in all_results.values() if r.get('_error') or r.get('_parse_error')]

print(f"Successfully extracted : {len(valid)}/{len(all_results)} documents")
print(f"Errors / parse failures: {len(errors)}/{len(all_results)} documents")

confs = [r.get('extraction_confidence', 'unknown') for r in valid]
print("\nExtraction confidence:")
for level in ['high', 'medium', 'low', 'unknown']:
    count = confs.count(level)
    pct   = round(count / len(valid) * 100) if valid else 0
    print(f"  {level.capitalize():8}: {count}/{len(valid)} ({pct}%)")

all_issues = []
for r in valid:
    all_issues.extend(r.get('quality_issues', []))
if all_issues:
    print("\nQuality issues identified by model:")
    for issue, count in Counter(all_issues).most_common():
        print(f"  {issue}: {count} documents")

avg_time = sum(r.get('_time', 0) for r in all_results.values()) / len(all_results)
print(f"\nAverage processing time: {avg_time:.1f}s per document")
print(f"Estimated time for 50,000 documents: {50000 * avg_time / 3600:.1f} hours")
