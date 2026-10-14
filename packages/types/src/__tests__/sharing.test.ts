import { describe, expect, it } from "vitest";
import {
  CreateShareResponseSchema,
  ExportJobsResponseSchema,
  ShareListResponseSchema,
} from "../sharing";

const share = {
  id: "s1",
  version_no: null,
  expires_at: "2026-10-21T10:00:00+00:00",
  max_downloads: 3,
  note: "Notaire",
  created_by: "u1",
  created_by_email: "a@example.com",
  created_at: "2026-10-14T10:00:00+00:00",
  revoked_at: null,
  revoked_by: null,
  has_password: true,
  download_count: 1,
  last_downloaded_at: "2026-10-14T11:00:00+00:00",
  status: "active",
};

describe("share links", () => {
  it("parses a created link with its one-time URL", () => {
    const res = CreateShareResponseSchema.parse({ share, url: "https://ged.example.org/s/abc" });
    expect(res.url).toContain("/s/");
    expect(res.share.version_no).toBeNull();
  });

  it("rejects an unknown status", () => {
    expect(() => ShareListResponseSchema.parse({ shares: [{ ...share, status: "paused" }] })).toThrow();
  });
});

describe("export jobs", () => {
  it("parses running and finished jobs with warnings", () => {
    const list = ExportJobsResponseSchema.parse({
      jobs: [
        {
          id: "j1",
          status: "done",
          selection: { kind: "search", search: { q: "décret" }, versions: "current" },
          versions: "current",
          document_count: 2,
          file_count: 2,
          total_bytes: 2048,
          zip_sha256: "ab".repeat(32),
          warnings: [{ document: "d1", version_no: 1, problem: "hash_mismatch" }],
          error: null,
          created_at: "2026-10-14T10:00:00+00:00",
          started_at: "2026-10-14T10:00:01+00:00",
          finished_at: "2026-10-14T10:00:02+00:00",
          expires_at: "2026-10-15T10:00:02+00:00",
        },
        {
          id: "j2",
          status: "queued",
          selection: { kind: "documents" },
          versions: "all",
          document_count: 1,
          file_count: 3,
          total_bytes: 10,
          zip_sha256: null,
          warnings: null,
          error: null,
          created_at: "2026-10-14T10:00:00+00:00",
          started_at: null,
          finished_at: null,
          expires_at: null,
        },
      ],
    });
    expect(list.jobs[0].warnings![0].problem).toBe("hash_mismatch");
    expect(list.jobs[1].versions).toBe("all");
  });
});
