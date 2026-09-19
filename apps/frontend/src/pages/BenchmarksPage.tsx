import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useDocuments, useOcrResult } from "../hooks/useDocuments";
import { useVlmDocuments, useVlmResult } from "../hooks/useVlm";
import NavSidebar from "../components/NavSidebar";
import DocumentList from "../components/DocumentList";
import OcrPanel from "../components/OcrPanel";
import VlmPanel from "../components/VlmPanel";

type Tab = "vlm" | "ocr";

const SKELETON = (
  <div className="p-8 space-y-4">
    <div className="h-6 bg-gray-200 rounded animate-pulse w-1/3" />
    <div className="h-64 bg-gray-200 rounded animate-pulse" />
    <div className="grid grid-cols-3 gap-4">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-48 bg-gray-200 rounded animate-pulse" />
      ))}
    </div>
  </div>
);

export default function BenchmarksPage() {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("vlm");

  const { data: ocrDocs, isLoading: ocrDocsLoading, error: ocrDocsError } = useDocuments();
  const { data: vlmDocs, isLoading: vlmDocsLoading, error: vlmDocsError } = useVlmDocuments();

  const documents   = tab === "vlm" ? vlmDocs    : ocrDocs;
  const docsLoading = tab === "vlm" ? vlmDocsLoading : ocrDocsLoading;
  const docsError   = tab === "vlm" ? vlmDocsError   : ocrDocsError;

  const { data: ocrDoc,    isLoading: ocrLoading,  error: ocrError  } = useOcrResult(tab === "ocr" ? selected : null);
  const { data: vlmResult, isLoading: vlmLoading,  error: vlmError  } = useVlmResult(tab === "vlm" ? selected : null);

  const isLoading  = tab === "ocr" ? ocrLoading : vlmLoading;
  const fetchError = tab === "ocr" ? ocrError   : vlmError;

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />

      {/* Secondary panel — document list for this benchmark tool */}
      <aside className="w-72 flex-shrink-0 border-r border-gray-200 bg-white flex flex-col overflow-hidden">
        <div className="px-4 py-4 border-b border-gray-200">
          <h1 className="text-sm font-bold text-gray-900">{t("benchmarks.header")}</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {t("benchmarks.subtitle")}
          </p>
        </div>

        <div className="flex border-b border-gray-200">
          {(["vlm", "ocr"] as Tab[]).map((tb) => (
            <button
              key={tb}
              onClick={() => { setTab(tb); setSelected(null); }}
              className={`flex-1 py-2 text-xs font-semibold transition-colors ${
                tab === tb
                  ? "border-b-2 border-indigo-600 text-indigo-700"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              {tb === "vlm" ? t("benchmarks.tabVlm") : t("benchmarks.tabOcr")}
            </button>
          ))}
        </div>

        <div className="overflow-y-auto flex-1">
          {docsError ? (
            <p className="p-4 text-sm text-red-500">
              {tab === "vlm"
                ? t("benchmarks.vlmNotFound")
                : t("benchmarks.loadError")}
            </p>
          ) : (
            <DocumentList
              documents={documents ?? []}
              selected={selected}
              onSelect={setSelected}
              isLoading={docsLoading}
            />
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        {!selected ? (
          <div className="flex h-full items-center justify-center text-gray-400 text-sm">
            {t("benchmarks.selectDocument", { kind: tab === "vlm" ? t("benchmarks.kindVlm") : t("benchmarks.kindOcr") })}
          </div>
        ) : isLoading ? (
          SKELETON
        ) : fetchError ? (
          <div className="p-8 text-sm text-red-500">
            {t("benchmarks.noResultFor", { kind: tab === "vlm" ? "VLM" : "OCR" })}{" "}
            <strong>{selected}</strong>.{" "}
            {tab === "vlm" && t("benchmarks.runVlmScript")}
          </div>
        ) : (
          <div className="p-8">
            <h2 className="text-xl font-semibold text-gray-800 truncate mb-6">{selected}</h2>
            {tab === "ocr" && ocrDoc    && <OcrPanel   doc={ocrDoc}       />}
            {tab === "vlm" && vlmResult && <VlmPanel   result={vlmResult} />}
          </div>
        )}
      </main>
    </div>
  );
}
