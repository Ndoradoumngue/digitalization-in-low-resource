import { useTranslation } from "react-i18next";

// Only non-default states get a badge - an active document shows nothing.
const STYLES: Record<string, string> = {
  archived: "bg-sky-100 text-sky-800",
  destruction_eligible: "bg-orange-100 text-orange-800",
  destroyed: "bg-gray-800 text-white",
};

export default function LifecycleBadge({
  status,
  legalHold = false,
}: {
  status: string | null | undefined;
  legalHold?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <>
      {status && STYLES[status] && (
        <span
          className={`inline-flex flex-shrink-0 items-center whitespace-nowrap px-2 py-0.5 rounded text-[11px] font-semibold ${STYLES[status]}`}
          title={t("lifecycle.label")}
        >
          {t(`lifecycle.status.${status}`)}
        </span>
      )}
      {legalHold && (
        <span
          className="inline-flex flex-shrink-0 items-center gap-1 whitespace-nowrap px-2 py-0.5 rounded text-[11px] font-semibold bg-purple-100 text-purple-800"
          title={t("lifecycle.legalHoldHint")}
        >
          <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24" aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
          </svg>
          {t("lifecycle.legalHold")}
        </span>
      )}
    </>
  );
}
