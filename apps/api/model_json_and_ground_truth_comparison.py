import json
from collections import defaultdict

with open('documents/ocr_results/json/vlm_qwen25_results.json') as f:
    model_results = json.load(f)
with open('documents/ocr_results/json/ground_truth.json') as f:
    ground_truth = json.load(f)

FIELDS = ['document_type', 'reference_number', 'date', 'organisation', 'signatory']

def normalize(s):
    if not s:
        return ""
    import unicodedata
    s = str(s).strip().lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = s.replace("'", "").replace("'", "").replace(",", "").replace(".", "")
    s = ' '.join(s.split())
    return s

def is_evaluable(val):
    if not val:
        return False
    return '*' not in str(val)

results_by_tier = defaultdict(lambda: {
    'correct': 0, 'total': 0, 'skipped': 0,
    'per_field': defaultdict(lambda: {'correct': 0, 'total': 0, 'skipped': 0})
})

mismatches_detail = []

for fname, gt_entry in ground_truth.items():
    gt = gt_entry['ground_truth']
    model = model_results.get(fname, {})
    tier = model.get('extraction_confidence', 'unknown')

    for field in FIELDS:
        gt_val = gt.get(field, '') or ''
        model_val = str(model.get(field, '') or '')

        if not is_evaluable(gt_val):
            results_by_tier[tier]['skipped'] += 1
            results_by_tier[tier]['per_field'][field]['skipped'] += 1
            continue

        results_by_tier[tier]['total'] += 1
        results_by_tier[tier]['per_field'][field]['total'] += 1

        match = normalize(gt_val) == normalize(model_val)
        if match:
            results_by_tier[tier]['correct'] += 1
            results_by_tier[tier]['per_field'][field]['correct'] += 1
        else:
            mismatches_detail.append({
                'file': fname,
                'tier': tier,
                'field': field,
                'gt': gt_val,
                'model': model_val
            })

print("=== ACCURACY BY CONFIDENCE TIER (excluding anonymised fields) ===\n")
for tier in ['high', 'medium', 'low', 'unknown']:
    r = results_by_tier[tier]
    if r['total'] == 0:
        continue
    pct = r['correct'] / r['total'] * 100
    print(f"[{tier.upper()}] Overall: {pct:.1f}% ({r['correct']}/{r['total']}) | Skipped (anonymised): {r['skipped']}")
    for field in FIELDS:
        fr = r['per_field'][field]
        if fr['total'] == 0:
            print(f"  {field:20s}: — (all anonymised, n={fr['skipped']})")
        else:
            fpct = fr['correct'] / fr['total'] * 100
            print(f"  {field:20s}: {fpct:.1f}% ({fr['correct']}/{fr['total']}) | skipped={fr['skipped']}")
    print()

print("\n=== MISMATCHES DETAIL ===\n")
for m in mismatches_detail:
    print(f"[{m['tier'].upper()}] {m['file']} — {m['field']}")
    print(f"  GT   : '{m['gt']}'")
    print(f"  Model: '{m['model']}'")
    print()