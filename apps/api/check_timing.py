import json
from collections import Counter

with open('documents/ocr_results/json/vlm_qwen25_results.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

def categorize(doc_type):
    if not doc_type:
        return "unclassified"
    t = doc_type.upper()
    if any(k in t for k in ['ARRET', 'ARRÊT', 'DECISION', 'DÉCISION', 'DECRET', 'DÉCRET', 'CONVENTION', 'PROTOCOLE']):
        return "regulatory_legal"
    if any(k in t for k in ['CONTRAT', 'AUTORISATION', 'ATTESTATION', 'CERTIFICAT', 'DIPLOME', 'DIPLÔME', 'DECHARGE']):
        return "human_resources"
    if any(k in t for k in ['FACTURE', 'DEVIS', 'BUDGET', 'COMPTE', 'RECAP', 'AVIS DE CREDIT', 'RELEVE', 'RELEVÉ', 'CHEQUE', 'CHÈQUE', 'ORDRE DE VIREMENT']):
        return "financial_operational"
    return "administrative_correspondence"

counts = Counter(categorize(r.get('document_type')) for r in data.values() if not r.get('_error') and not r.get('_parse_error'))
total = sum(counts.values())
for cat, n in counts.most_common():
    print(f"{cat}: {n} ({n/total*100:.1f}%)")