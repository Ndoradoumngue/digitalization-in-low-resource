import { useTranslation } from "react-i18next";

const LANGUAGES = [
  { code: "en", label: "EN" },
  { code: "fr", label: "FR" },
] as const;

export default function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const current = i18n.language?.startsWith("fr") ? "fr" : "en";

  return (
    <div
      role="group"
      aria-label={t("languageSwitcher.label")}
      className="inline-flex items-center rounded-md border border-gray-200 bg-gray-50 p-0.5"
    >
      {LANGUAGES.map((l) => (
        <button
          key={l.code}
          onClick={() => i18n.changeLanguage(l.code)}
          className={`px-2 py-0.5 rounded text-[11px] font-semibold transition-colors ${
            current === l.code
              ? "bg-white text-indigo-700 shadow-sm"
              : "text-gray-400 hover:text-gray-600"
          }`}
        >
          {l.label}
        </button>
      ))}
    </div>
  );
}
