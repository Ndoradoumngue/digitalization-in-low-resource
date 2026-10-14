import type { SnippetSegment } from "@sdai/types";

// A search snippet with its matching words highlighted. Segments come from
// the API already split (documents_router._segments) and are rendered as
// plain text - document content is never interpreted as markup.
export default function SearchSnippet({
  segments,
  className = "",
}: {
  segments: SnippetSegment[];
  className?: string;
}) {
  if (segments.length === 0) return null;
  return (
    <p className={`text-xs text-gray-600 leading-relaxed whitespace-pre-line ${className}`}>
      {segments.map((s, i) =>
        s.hit ? (
          <mark key={i} className="bg-amber-100 text-gray-900 rounded px-0.5">
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </p>
  );
}
