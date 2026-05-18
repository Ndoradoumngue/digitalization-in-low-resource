import { describe, it, expect } from "vitest";
import {
  EngineResultSchema,
  VariantResultSchema,
  OcrDocumentSchema,
  DocumentEntrySchema,
  DocumentListSchema,
  ENGINE_LABELS,
  type EngineName,
} from "../ocr";

const validEngine = {
  text: "texte extrait",
  confidence: 0.92,
  time: 1.23,
  error: null,
};

const validVariant = {
  tesseract: validEngine,
  easyocr:   validEngine,
  surya:     validEngine,
};

// ── EngineResultSchema ────────────────────────────────────────────────────────

describe("EngineResultSchema", () => {
  it("parses a valid result with null error", () => {
    const parsed = EngineResultSchema.parse(validEngine);
    expect(parsed.confidence).toBe(0.92);
    expect(parsed.error).toBeNull();
  });

  it("parses a result with an error string", () => {
    const parsed = EngineResultSchema.parse({ ...validEngine, error: "GPU OOM" });
    expect(parsed.error).toBe("GPU OOM");
  });

  it("rejects missing required fields", () => {
    expect(() => EngineResultSchema.parse({ text: "x", confidence: 0.9 })).toThrow();
  });
});

// ── VariantResultSchema ───────────────────────────────────────────────────────

describe("VariantResultSchema", () => {
  it("parses a full variant with all three engines", () => {
    const parsed = VariantResultSchema.parse(validVariant);
    expect(parsed.tesseract.text).toBe("texte extrait");
    expect(parsed.surya.confidence).toBe(0.92);
  });

  it("rejects a variant missing an engine", () => {
    const { surya: _, ...noSurya } = validVariant;
    expect(() => VariantResultSchema.parse(noSurya)).toThrow();
  });
});

// ── OcrDocumentSchema ─────────────────────────────────────────────────────────

describe("OcrDocumentSchema", () => {
  it("parses a complete OCR document", () => {
    const doc = { filename: "doc1.png", raw: validVariant, preprocessed: validVariant };
    const parsed = OcrDocumentSchema.parse(doc);
    expect(parsed.filename).toBe("doc1.png");
  });

  it("rejects a document with a missing variant", () => {
    const doc = { filename: "doc1.png", raw: validVariant };
    expect(() => OcrDocumentSchema.parse(doc)).toThrow();
  });
});

// ── DocumentEntrySchema ───────────────────────────────────────────────────────

describe("DocumentEntrySchema", () => {
  it("parses a document entry with hasResult true", () => {
    const parsed = DocumentEntrySchema.parse({ filename: "doc1.png", hasResult: true });
    expect(parsed.hasResult).toBe(true);
  });

  it("parses a document entry with hasResult false", () => {
    const parsed = DocumentEntrySchema.parse({ filename: "doc2.jpg", hasResult: false });
    expect(parsed.hasResult).toBe(false);
  });

  it("rejects a non-boolean hasResult", () => {
    expect(() =>
      DocumentEntrySchema.parse({ filename: "doc1.png", hasResult: "yes" })
    ).toThrow();
  });
});

// ── DocumentListSchema ────────────────────────────────────────────────────────

describe("DocumentListSchema", () => {
  it("parses an array of entries", () => {
    const list = [
      { filename: "a.png", hasResult: true },
      { filename: "b.jpg", hasResult: false },
    ];
    const parsed = DocumentListSchema.parse(list);
    expect(parsed).toHaveLength(2);
  });

  it("parses an empty array", () => {
    expect(DocumentListSchema.parse([])).toEqual([]);
  });
});

// ── ENGINE_LABELS ─────────────────────────────────────────────────────────────

describe("ENGINE_LABELS", () => {
  it("has a label for every engine name", () => {
    const engines: EngineName[] = ["tesseract", "easyocr", "surya"];
    for (const e of engines) {
      expect(ENGINE_LABELS[e]).toBeTruthy();
    }
  });
});
