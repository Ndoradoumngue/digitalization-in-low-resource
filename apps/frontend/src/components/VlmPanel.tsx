import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  return (
    <table className="w-full">
      <tbody className="divide-y divide-gray-50">
        <FieldRow label={t("vlm.fields.document_type")} value={fields.document_type ?? null} />
        <FieldRow label={t("vlm.fields.reference_number")} value={fields.reference_number ?? null} />
        <FieldRow label={t("vlm.fields.date")} value={fields.date ?? null} />
        <FieldRow label={t("vlm.fields.person_names")} value={fields.person_names ?? null} />
        <FieldRow label={t("vlm.fields.functions")} value={fields.functions ?? null} />
        <FieldRow label={t("vlm.fields.destination_or_subject")} value={fields.destination_or_subject ?? null} />
        <FieldRow label={t("vlm.fields.organisation")} value={fields.organisation ?? null} />
        <FieldRow label={t("vlm.fields.signatory")} value={fields.signatory ?? null} />
        <FieldRow label={t("vlm.fields.budget_line")} value={fields.budget_line ?? null} />
        <FieldRow label={t("vlm.fields.language")} value={fields.language ?? null} />
        <FieldRow label={t("vlm.fields.bilingual_layout")} value={fields.bilingual_layout ?? null} />
        <FieldRow label={t("vlm.fields.quality_issues")} value={fields.quality_issues ?? null} />
      </tbody>
    </table>
  );
}

function SuccessView({ result }: { result: SuccessResult }) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
      <div>
        <h3 className="text-sm font-medium text-gray-500 mb-2">{t("vlm.documentImage")}</h3>
        <ImageViewer filename={result.filename} />
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium text-gray-500">{t("vlm.extractedFields")}</h3>
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
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <TierBadge tier="failed" />
        <span className="text-sm text-gray-500">{result.processing_time}s</span>
      </div>

      {result.error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          <span className="font-semibold">{t("vlm.runtimeError")}</span>
          {result.error}
        </div>
      )}

      {result.is_parse_error && result.raw_response && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-amber-700 font-medium">
            {t("vlm.parseError")}
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
