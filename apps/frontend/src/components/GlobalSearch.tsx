import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

// Search box at the top of the nav, on every page: searches the extracted
// fields and the text of every page of every document the user may read
// (GET /api/db/documents?q=), results on the Documents page.
export default function GlobalSearch() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const current = location.pathname === "/documents"
    ? new URLSearchParams(location.search).get("q") ?? ""
    : "";
  const [value, setValue] = useState(current);
  useEffect(() => { setValue(current); }, [current]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const q = value.trim();
    navigate(q ? `/documents?q=${encodeURIComponent(q)}` : "/documents");
  }

  return (
    <form role="search" onSubmit={submit} className="px-3 pt-3">
      <label className="relative block">
        <span className="sr-only">{t("search.label")}</span>
        <svg
          className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none"
          fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
        </svg>
        <input
          type="search"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={t("search.placeholder")}
          className="h-9 w-full rounded-md border border-gray-300 pl-8 pr-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </label>
    </form>
  );
}
