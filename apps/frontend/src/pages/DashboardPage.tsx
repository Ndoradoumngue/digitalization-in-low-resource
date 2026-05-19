import { useState } from "react";
import { Link } from "react-router-dom";
import { useDocuments, useOcrResult } from "../hooks/useDocuments";
import { useVlmDocuments, useVlmResult } from "../hooks/useVlm";
import { useAuth } from "../context/AuthContext";
import { useDbTypes } from "../hooks/useDocumentsDb";
import { useReviewCount } from "../hooks/useReview";
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

export default function DashboardPage() {
  const { user, logout } = useAuth();
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("vlm");
  const { data: dbTypes }     = useDbTypes();
  const dbTotal               = (dbTypes ?? []).reduce((acc, t) => acc + t.count, 0);
  const { data: reviewCount } = useReviewCount();
  const pendingReview         = reviewCount?.pending ?? 0;

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
      {/* Sidebar */}
      <aside className="w-72 flex-shrink-0 border-r border-gray-200 bg-white flex flex-col overflow-hidden">
        {/* Header */}
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

        {/* Document list */}
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

        {/* User / logout footer */}
        <div className="px-4 py-3 border-t border-gray-200 space-y-2">
          <Link
            to="/documents"
            className="flex items-center gap-2 w-full rounded-lg px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 0 1-2.247 2.118H6.622a2.25 2.25 0 0 1-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125Z" />
            </svg>
            Documents
            {dbTotal > 0 && (
              <span className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-700">
                {dbTotal.toLocaleString()}
              </span>
            )}
          </Link>
          <Link
            to="/schema"
            className="flex items-center gap-2 w-full rounded-lg px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6" />
            </svg>
            Schema
          </Link>
          <Link
            to="/review"
            className="flex items-center gap-2 w-full rounded-lg px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
            </svg>
            Review Queue
            {pendingReview > 0 && (
              <span className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-bold bg-red-500 text-white">
                {pendingReview}
              </span>
            )}
          </Link>
          {user?.role === "admin" && (
            <Link
              to="/admin/audit"
              className="flex items-center gap-2 w-full rounded-lg px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-100 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25Z" />
              </svg>
              Audit Log
            </Link>
          )}
          <Link
            to="/upload"
            className="flex items-center gap-2 w-full rounded-lg px-3 py-2 text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
            </svg>
            Upload documents
          </Link>
          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-gray-700 truncate">{user?.full_name ?? user?.email}</p>
              {user?.role && (
                <span className={`inline-block mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                  user.role === "admin"
                    ? "bg-indigo-100 text-indigo-700"
                    : "bg-gray-100 text-gray-600"
                }`}>
                  {user.role}
                </span>
              )}
            </div>
            <button
              onClick={logout}
              className="flex-shrink-0 text-xs text-gray-400 hover:text-red-500 transition-colors"
            >
              Sign out
            </button>
          </div>
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
            <h2 className="text-xl font-semibold text-gray-800 truncate mb-6">{selected}</h2>
            {tab === "ocr" && ocrDoc    && <OcrPanel   doc={ocrDoc}       />}
            {tab === "vlm" && vlmResult && <VlmPanel   result={vlmResult} />}
          </div>
        )}
      </main>
    </div>
  );
}
