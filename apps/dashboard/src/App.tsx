import { useState } from "react";
import { useDocuments, useOcrResult } from "./hooks/useDocuments";
import { useVlmDocuments, useVlmResult } from "./hooks/useVlm";
import DocumentList from "./components/DocumentList";
import OcrPanel from "./components/OcrPanel";
import VlmPanel from "./components/VlmPanel";

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

export default function App() {
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("vlm");

  // Both document lists are fetched eagerly so switching tabs is instant.
  // Each list carries its own hasResult flag reflecting that backend's data.
  const { data: ocrDocs, isLoading: ocrDocsLoading, error: ocrDocsError } = useDocuments();
  const { data: vlmDocs, isLoading: vlmDocsLoading, error: vlmDocsError } = useVlmDocuments();

  const documents = tab === "vlm" ? vlmDocs : ocrDocs;
  const docsLoading = tab === "vlm" ? vlmDocsLoading : ocrDocsLoading;
  const docsError = tab === "vlm" ? vlmDocsError : ocrDocsError;

  // Only fetch the active tab's result — no wasted request when switching
  const { data: ocrDoc, isLoading: ocrLoading, error: ocrError } = useOcrResult(
    tab === "ocr" ? selected : null
  );
  const { data: vlmResult, isLoading: vlmLoading, error: vlmError } = useVlmResult(
    tab === "vlm" ? selected : null
  );

  const isLoading = tab === "ocr" ? ocrLoading : vlmLoading;
  const fetchError = tab === "ocr" ? ocrError : vlmError;

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      {/* Sidebar */}
      <aside className="w-72 flex-shrink-0 border-r border-gray-200 bg-white flex flex-col overflow-hidden">
        <div className="px-4 py-4 border-b border-gray-200">
          <h1 className="text-lg font-bold text-indigo-700 tracking-tight">
            SDAI Digitalization
          </h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {documents ? `${documents.length} documents` : "Loading…"}
          </p>
        </div>

        {/* Tab switcher */}
        <div className="flex border-b border-gray-200">
          {(["vlm", "ocr"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); setSelected(null); }}
              className={`flex-1 py-2 text-xs font-semibold transition-colors ${
                tab === t
                  ? "border-b-2 border-indigo-600 text-indigo-700"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              {t === "vlm" ? "VLM Extraction" : "OCR Comparison"}
            </button>
          ))}
        </div>

        <div className="overflow-y-auto flex-1">
          {docsError ? (
            <p className="p-4 text-sm text-red-500">
              {tab === "vlm"
                ? "VLM results not found. Run vlm_ollama_test.py first."
                : "Failed to load documents. Is the API server running?"}
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
            Select a document to view its{" "}
            {tab === "vlm" ? "VLM extraction" : "OCR comparison"} results.
          </div>
        ) : isLoading ? (
          SKELETON
        ) : fetchError ? (
          <div className="p-8 text-sm text-red-500">
            No {tab === "vlm" ? "VLM" : "OCR"} result for{" "}
            <strong>{selected}</strong>.{" "}
            {tab === "vlm" && "Run vlm_ollama_test.py to generate it."}
          </div>
        ) : (
          <div className="p-8">
            <h2 className="text-xl font-semibold text-gray-800 truncate mb-6">
              {selected}
            </h2>
            {tab === "ocr" && ocrDoc && <OcrPanel doc={ocrDoc} />}
            {tab === "vlm" && vlmResult && <VlmPanel result={vlmResult} />}
          </div>
        )}
      </main>
    </div>
  );
}
