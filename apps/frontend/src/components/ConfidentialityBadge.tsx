import { useTranslation } from "react-i18next";
import type { Confidentiality } from "@sdai/types";

const STYLES: Record<Confidentiality, string> = {
  public: "bg-emerald-100 text-emerald-700",
  internal: "bg-gray-100 text-gray-600",
  confidential: "bg-amber-100 text-amber-800",
  secret: "bg-red-100 text-red-700",
};

export function asConfidentiality(value: unknown): Confidentiality | undefined {
  return typeof value === "string" && value in STYLES ? (value as Confidentiality) : undefined;
}

export default function ConfidentialityBadge({ level }: { level: Confidentiality }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold ${STYLES[level]}`}
      title={t("confidentiality.label")}
    >
      {t(`confidentiality.${level}`)}
    </span>
  );
}
