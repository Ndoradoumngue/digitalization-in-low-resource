import type {
  ExtractionResult,
  SuccessResult,
  FailedResult,
  DocumentFields,
} from "@sdai/types";
import ImageViewer from "./ImageViewer";

interface Props {
  result: ExtractionResult;
}

// Tier badge styles indexed by tier
const TIER_STYLES: Record<ExtractionResult["tier"], string> = {
  high: "bg-emerald-100 text-emerald-800 border-emerald-200",
  medium: "bg-amber-100 text-amber-800 border-amber-200",
  low: "bg-orange-100 text-orange-800 border-orange-200",
  failed: "bg-red-100 text-red-800 border-red-200",
};

function TierBadge({ tier }: { tier: ExtractionResult["tier"] }) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${TIER_STYLES[tier]}`}
    >
      {tier.toUpperCase()}
    </span>
  );
}

function FieldRow({ label, value }: { label: string; value: string | string[] | null }) {
  if (value === null || (Array.isArray(value) && value.length === 0)) {
    return (
      <tr>
        <td className="py-1.5 pr-4 text-xs font-medium text-gray-500 whitespace-nowrap">{label}</td>
        <td className="py-1.5 text-xs text-gray-300 italic">—</td>
      </tr>
    );
  }
  return (
    <tr>
      <td className="py-1.5 pr-4 text-xs font-medium text-gray-500 whitespace-nowrap">{label}</td>
      <td className="py-1.5 text-xs text-gray-800">
        {Array.isArray(value) ? value.join(", ") : value}
      </td>
    </tr>
  );
}

function FieldsTable({ fields }: { fields: DocumentFields | Partial<DocumentFields> }) {
  return (
    <table className="w-full">
      <tbody className="divide-y divide-gray-50">
        <FieldRow label="Document type" value={fields.document_type ?? null} />
        <FieldRow label="Reference" value={fields.reference_number ?? null} />
        <FieldRow label="Date" value={fields.date ?? null} />
        <FieldRow label="People" value={fields.person_names ?? null} />
        <FieldRow label="Functions" value={fields.functions ?? null} />
        <FieldRow label="Subject / destination" value={fields.destination_or_subject ?? null} />
        <FieldRow label="Organisation" value={fields.organisation ?? null} />
        <FieldRow label="Signatory" value={fields.signatory ?? null} />
        <FieldRow label="Budget line" value={fields.budget_line ?? null} />
        <FieldRow label="Language" value={fields.language ?? null} />
        <FieldRow label="Bilingual layout" value={fields.bilingual_layout ?? null} />
        <FieldRow label="Quality issues" value={fields.quality_issues ?? null} />
      </tbody>
    </table>
  );
}

function SuccessView({ result }: { result: SuccessResult }) {
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
      <div>
        <h3 className="text-sm font-medium text-gray-500 mb-2">Document image</h3>
        <ImageViewer filename={result.filename} />
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium text-gray-500">Extracted fields</h3>
          <TierBadge tier={result.tier} />
          <span className="ml-auto text-xs text-gray-400">{result.processing_time}s</span>
        </div>

        <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
          <div className="px-4 py-3">
            <FieldsTable fields={result.fields} />
          </div>
        </div>
      </div>
    </div>
  );
}

function FailedView({ result }: { result: FailedResult }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <TierBadge tier="failed" />
        <span className="text-sm text-gray-500">{result.processing_time}s</span>
      </div>

      {result.error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          <span className="font-semibold">Runtime error: </span>
          {result.error}
        </div>
      )}

      {result.is_parse_error && result.raw_response && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-amber-700 font-medium">
            Model output could not be parsed as JSON.
          </p>
          <pre className="p-4 bg-gray-50 border border-gray-200 rounded-lg text-xs text-gray-600 overflow-auto max-h-64 whitespace-pre-wrap">
            {result.raw_response}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function VlmPanel({ result }: Props) {
  // Discriminated union narrowing — each branch has a distinct type
  if (result.tier === "failed") {
    return <FailedView result={result} />;
  }
  return <SuccessView result={result} />;
}
