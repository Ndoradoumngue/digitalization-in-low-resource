import type { EngineResult } from "@sdai/types";
import ConfidenceBadge from "./ConfidenceBadge";

interface Props {
  label: string;
  result: EngineResult;
}

export default function EngineCard({ label, result }: Props) {
  return (
    <div className="flex flex-col border border-gray-200 rounded-lg overflow-hidden bg-white">
      <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200">
        <span className="font-semibold text-gray-800 text-sm">{label}</span>
        <div className="flex items-center gap-2">
          <ConfidenceBadge value={result.confidence} />
          <span className="text-xs text-gray-400">{result.time}s</span>
        </div>
      </div>

      <div className="h-1 bg-gray-100">
        <div
          className={`h-full transition-all ${
            result.confidence >= 0.8
              ? "bg-emerald-400"
              : result.confidence >= 0.5
              ? "bg-amber-400"
              : "bg-red-400"
          }`}
          style={{ width: `${result.confidence * 100}%` }}
        />
      </div>

      {result.error && (
        <div className="px-4 py-3 text-xs text-red-600 bg-red-50 border-b border-red-100">
          Error: {result.error}
        </div>
      )}

      <div className="p-4 flex-1 overflow-auto max-h-72">
        {result.text ? (
          <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed font-mono">
            {result.text}
          </p>
        ) : (
          <p className="text-sm text-gray-400 italic">No text extracted.</p>
        )}
      </div>

      <div className="px-4 py-2 border-t border-gray-100 bg-gray-50 text-xs text-gray-500 flex gap-4">
        <span>{result.text.length} chars</span>
        <span>{result.text.split(/\s+/).filter(Boolean).length} words</span>
      </div>
    </div>
  );
}
