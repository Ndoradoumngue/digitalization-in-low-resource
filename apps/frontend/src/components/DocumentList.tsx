import type { DocumentEntry } from "@sdai/types";

interface Props {
  documents: DocumentEntry[];
  selected: string | null;
  onSelect: (filename: string) => void;
  isLoading: boolean;
}

export default function DocumentList({ documents, selected, onSelect, isLoading }: Props) {
  if (isLoading) {
    return (
      <div className="p-4 space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-10 bg-gray-200 rounded animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <ul className="divide-y divide-gray-200">
      {documents.map((doc) => (
        <li key={doc.filename}>
          <button
            onClick={() => onSelect(doc.filename)}
            className={`w-full text-left px-4 py-3 text-sm hover:bg-indigo-50 transition-colors flex items-center gap-2 min-w-0 ${
              selected === doc.filename
                ? "bg-indigo-100 font-medium text-indigo-700"
                : "text-gray-700"
            }`}
          >
            {/* hasResult dot: green = results exist, gray = not yet processed */}
            <span
              className={`flex-shrink-0 w-1.5 h-1.5 rounded-full ${
                doc.hasResult ? "bg-emerald-400" : "bg-gray-300"
              }`}
            />
            <span className="truncate">{doc.filename}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
