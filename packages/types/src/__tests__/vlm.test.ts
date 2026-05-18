import { describe, it, expect } from "vitest";
import {
  parseVlmResult,
  ExtractionResultSchema,
  RawVlmResultSchema,
  type RawVlmResult,
} from "../vlm";

const base: Pick<RawVlmResult, "_filename" | "_time"> = {
  _filename: "doc1.png",
  _time: 2.5,
};

// ── parseVlmResult ────────────────────────────────────────────────────────────

describe("parseVlmResult", () => {
  it("maps high confidence to high tier", () => {
    const result = parseVlmResult({
      ...base,
      extraction_confidence: "high",
      document_type: "ordre_de_mission",
      quality_issues: ["faded_ink"],
    });
    expect(result.tier).toBe("high");
    expect(result.filename).toBe("doc1.png");
    expect(result.processing_time).toBe(2.5);
    if (result.tier === "high") {
      expect(result.fields.document_type).toBe("ordre_de_mission");
      expect(result.fields.quality_issues).toEqual(["faded_ink"]);
    }
  });

  it("maps medium confidence to medium tier", () => {
    const result = parseVlmResult({ ...base, extraction_confidence: "medium" });
    expect(result.tier).toBe("medium");
  });

  it("maps low confidence to low tier", () => {
    const result = parseVlmResult({ ...base, extraction_confidence: "low" });
    expect(result.tier).toBe("low");
  });

  it("defaults to low tier when extraction_confidence is absent", () => {
    const result = parseVlmResult({ ...base });
    expect(result.tier).toBe("low");
  });

  it("maps _error to failed tier with error message", () => {
    const result = parseVlmResult({ ...base, _error: "model timeout" });
    expect(result.tier).toBe("failed");
    if (result.tier === "failed") {
      expect(result.error).toBe("model timeout");
      expect(result.is_parse_error).toBe(false);
    }
  });

  it("maps _parse_error to failed tier with raw_response", () => {
    const result = parseVlmResult({ ...base, _parse_error: true, _raw: "{invalid json" });
    expect(result.tier).toBe("failed");
    if (result.tier === "failed") {
      expect(result.is_parse_error).toBe(true);
      expect(result.raw_response).toBe("{invalid json");
    }
  });

  it("falls back document_type to 'unknown' for high tier when null", () => {
    const result = parseVlmResult({
      ...base,
      extraction_confidence: "high",
      document_type: null,
    });
    if (result.tier === "high") {
      expect(result.fields.document_type).toBe("unknown");
    }
  });

  it("falls back quality_issues to [] for high tier when null", () => {
    const result = parseVlmResult({
      ...base,
      extraction_confidence: "high",
      document_type: "arrete",
      quality_issues: null,
    });
    if (result.tier === "high") {
      expect(result.fields.quality_issues).toEqual([]);
    }
  });

  it("nulls optional fields that are absent in the raw payload", () => {
    const result = parseVlmResult({ ...base, extraction_confidence: "medium" });
    if (result.tier === "medium") {
      expect(result.fields.document_type).toBeNull();
      expect(result.fields.person_names).toBeNull();
    }
  });
});

// ── ExtractionResultSchema ────────────────────────────────────────────────────

describe("ExtractionResultSchema", () => {
  const baseFields = {
    document_type: null,
    reference_number: null,
    date: null,
    person_names: null,
    functions: null,
    destination_or_subject: null,
    organisation: null,
    signatory: null,
    budget_line: null,
    language: null,
    bilingual_layout: null,
    quality_issues: null,
  };

  it("parses a valid high result", () => {
    const data = {
      tier: "high",
      filename: "doc1.png",
      processing_time: 1.0,
      fields: { ...baseFields, document_type: "arrete", quality_issues: [] },
    };
    const parsed = ExtractionResultSchema.parse(data);
    expect(parsed.tier).toBe("high");
  });

  it("parses a valid failed result", () => {
    const data = {
      tier: "failed",
      filename: "doc1.png",
      processing_time: 0.5,
      is_parse_error: true,
      raw_response: "{bad",
    };
    const parsed = ExtractionResultSchema.parse(data);
    expect(parsed.tier).toBe("failed");
    if (parsed.tier === "failed") {
      expect(parsed.is_parse_error).toBe(true);
    }
  });

  it("rejects an unknown tier", () => {
    expect(() =>
      ExtractionResultSchema.parse({
        tier: "unknown",
        filename: "x.png",
        processing_time: 0,
      })
    ).toThrow();
  });

  it("rejects a high result with missing required document_type", () => {
    const data = {
      tier: "high",
      filename: "doc1.png",
      processing_time: 1.0,
      fields: { ...baseFields, document_type: null, quality_issues: [] },
    };
    expect(() => ExtractionResultSchema.parse(data)).toThrow();
  });
});

// ── RawVlmResultSchema ────────────────────────────────────────────────────────

describe("RawVlmResultSchema", () => {
  it("validates a minimal raw payload (only required fields)", () => {
    const raw = { _filename: "doc1.png", _time: 1.0 };
    const parsed = RawVlmResultSchema.parse(raw);
    expect(parsed._filename).toBe("doc1.png");
    expect(parsed.extraction_confidence).toBeUndefined();
  });

  it("validates a full success payload", () => {
    const raw = {
      _filename: "doc1.png",
      _time: 3.1,
      extraction_confidence: "high",
      document_type: "correspondance",
      quality_issues: ["flag_stripes"],
      reference_number: null,
      date: null,
      person_names: null,
      functions: null,
      destination_or_subject: null,
      organisation: null,
      signatory: null,
      budget_line: null,
      language: "FR",
      bilingual_layout: null,
    };
    const parsed = RawVlmResultSchema.parse(raw);
    expect(parsed.document_type).toBe("correspondance");
  });

  it("validates an error payload", () => {
    const raw = {
      _filename: "doc1.png",
      _time: 0.2,
      _error: "connection refused",
    };
    const parsed = RawVlmResultSchema.parse(raw);
    expect(parsed._error).toBe("connection refused");
  });

  it("rejects a payload missing _filename", () => {
    expect(() => RawVlmResultSchema.parse({ _time: 1.0 })).toThrow();
  });
});
