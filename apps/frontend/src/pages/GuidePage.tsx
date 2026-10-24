import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Trans, useTranslation } from "react-i18next";
import type { User } from "@sdai/types";
import { useAuth } from "../context/AuthContext";
import NavSidebar from "../components/NavSidebar";
import {
  canEditExtraction,
  canManageAccess,
  canManageLifecycle,
  canManageUsers,
  canShare,
  isActor,
} from "../utils/permissions";
import { markGuideSeen } from "../utils/guide";

// "Getting started": how a document travels through the system and what the
// signed-in person can do with it. Sections a person can't act on are left
// out, the capability list shows the rest greyed out.

interface FlowStep {
  title: string;
  text: string;
}

type Section = {
  id: string;
  /** Who the section is for - everyone when absent. */
  show?: (u: User | null) => boolean;
  link?: { to: string; label: string };
};

const SECTIONS: Section[] = [
  { id: "find", link: { to: "/documents", label: "nav.documents" } },
  { id: "consult" },
  { id: "upload", show: isActor, link: { to: "/upload", label: "nav.uploadDocuments" } },
  { id: "review", show: isActor, link: { to: "/review", label: "nav.reviewQueue" } },
  { id: "versions", show: isActor },
  { id: "share", link: { to: "/exports", label: "nav.exports" } },
  { id: "collaborate", link: { to: "/notifications", label: "notifications.title" } },
  { id: "lifecycle", show: canManageLifecycle, link: { to: "/lifecycle/retention", label: "nav.retention" } },
  {
    id: "admin",
    show: (u) => u?.role === "admin" || canManageUsers(u),
    link: { to: "/ops/users", label: "nav.users" },
  },
];

// Flow steps the person takes part in (index into guide.flow.steps).
function myFlowSteps(user: User | null): Set<number> {
  const mine = new Set([5]);
  if (isActor(user)) [0, 3, 4].forEach((i) => mine.add(i));
  return mine;
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export default function GuidePage() {
  const { user } = useAuth();
  const { t } = useTranslation();

  useEffect(() => {
    markGuideSeen();
  }, []);

  const flow = t("guide.flow.steps", { returnObjects: true }) as FlowStep[];
  const mine = myFlowSteps(user);
  const sections = SECTIONS.filter((s) => !s.show || s.show(user));
  const caps: [string, boolean][] = [
    ["read", true],
    ["download", true],
    ["upload", isActor(user)],
    ["review", isActor(user)],
    ["editFields", canEditExtraction(user)],
    ["manageAccess", canManageAccess(user)],
    ["share", canShare(user)],
    ["lifecycle", canManageLifecycle(user)],
    ["users", canManageUsers(user)],
    ["admin", user?.role === "admin"],
  ];
  const toc = [
    { id: "flow", title: t("guide.flow.title") },
    { id: "account", title: t("guide.account.title") },
    ...sections.map((s) => ({ id: s.id, title: t(`guide.sections.${s.id}.title`) })),
    { id: "faq", title: t("guide.faq.title") },
  ];

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-6 py-8 flex gap-8">
          <div className="flex-1 min-w-0 space-y-10">
            <header>
              <h1 className="text-2xl font-bold text-gray-900">{t("guide.title")}</h1>
              <p className="text-sm text-gray-500 mt-1">{t("guide.subtitle")}</p>
            </header>

            {/* The journey of a document */}
            <section id="flow" className="scroll-mt-6">
              <h2 className="text-lg font-semibold text-gray-900">{t("guide.flow.title")}</h2>
              <p className="text-sm text-gray-500 mt-1">{t("guide.flow.intro")}</p>
              <ol className="mt-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {flow.map((step, i) => (
                  <li
                    key={i}
                    className={`relative rounded-xl border bg-white p-4 ${mine.has(i) ? "border-indigo-300" : "border-gray-200"}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-indigo-600 text-xs font-bold text-white">
                        {i + 1}
                      </span>
                      <h3 className="text-sm font-semibold text-gray-900">{step.title}</h3>
                      {i === 2 && (
                        <span className="text-[10px] font-medium text-gray-400">({t("guide.flow.optional")})</span>
                      )}
                      {mine.has(i) && (
                        <span className="ml-auto px-1.5 py-0.5 rounded text-[10px] font-semibold bg-indigo-100 text-indigo-700">
                          {t("guide.flow.you")}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-600 mt-2">{step.text}</p>
                  </li>
                ))}
              </ol>
            </section>

            {/* What this account can do */}
            <section id="account" className="scroll-mt-6">
              <h2 className="text-lg font-semibold text-gray-900">{t("guide.account.title")}</h2>
              <div className="mt-3 rounded-xl border border-gray-200 bg-white p-5 space-y-4">
                {user && (
                  <div className="text-sm text-gray-700 space-y-1">
                    <p>
                      <Trans
                        i18nKey="guide.account.role"
                        values={{ role: t(`roles.${user.role}`), tenant: user.tenant_name }}
                        components={{ b: <b className="font-semibold text-gray-900" /> }}
                      />
                    </p>
                    <p>
                      <Trans
                        i18nKey="guide.account.clearance"
                        values={{ level: t(`confidentiality.${user.clearance}`) }}
                        components={{ b: <b className="font-semibold text-gray-900" /> }}
                      />
                    </p>
                  </div>
                )}
                <div>
                  <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                    {t("guide.account.can")}
                  </h3>
                  <ul className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1.5">
                    {caps.map(([key, allowed]) => (
                      <li key={key} className={`flex items-start gap-2 text-sm ${allowed ? "text-gray-800" : "text-gray-400"}`}>
                        <span className={`mt-0.5 font-bold ${allowed ? "text-emerald-600" : "text-gray-300"}`}>
                          {allowed ? "✓" : "–"}
                        </span>
                        {t(`guide.account.caps.${key}`)}
                      </li>
                    ))}
                  </ul>
                  <p className="text-xs text-gray-400 mt-2">{t("guide.account.cannotNote")}</p>
                </div>
                <div>
                  <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                    {t("guide.account.tipsTitle")}
                  </h3>
                  <ul className="list-disc pl-5 space-y-1 text-sm text-gray-600">
                    {(t("guide.account.tips", { returnObjects: true }) as string[]).map((tip, i) => (
                      <li key={i}>{tip}</li>
                    ))}
                  </ul>
                  <Link to="/account" className="inline-block mt-3 text-sm font-medium text-indigo-700 hover:underline">
                    {t("guide.open", { page: t("nav.myProfile") })}
                  </Link>
                </div>
              </div>
            </section>

            {sections.map((s, n) => (
              <GuideSection key={s.id} section={s} number={n + 1} />
            ))}

            <section id="faq" className="scroll-mt-6">
              <h2 className="text-lg font-semibold text-gray-900">{t("guide.faq.title")}</h2>
              <div className="mt-3 rounded-xl border border-gray-200 bg-white divide-y divide-gray-100">
                {(t("guide.faq.items", { returnObjects: true }) as { q: string; a: string }[]).map((item, i) => (
                  <details key={i} className="group px-5 py-3">
                    <summary className="cursor-pointer text-sm font-medium text-gray-800 list-none flex items-center gap-2">
                      <span className="text-gray-400 transition-transform group-open:rotate-90">›</span>
                      {item.q}
                    </summary>
                    <p className="text-sm text-gray-600 mt-2 pl-4">{item.a}</p>
                  </details>
                ))}
              </div>
            </section>
          </div>

          {/* Table of contents */}
          <nav className="hidden xl:block w-52 flex-shrink-0">
            <div className="sticky top-8">
              <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-2">{t("guide.toc")}</p>
              <ul className="space-y-1 border-l border-gray-200">
                {toc.map((item) => (
                  <li key={item.id}>
                    <a
                      href={`#${item.id}`}
                      onClick={(e) => {
                        e.preventDefault();
                        scrollTo(item.id);
                      }}
                      className="block -ml-px border-l border-transparent pl-3 text-xs text-gray-600 hover:text-indigo-700 hover:border-indigo-400"
                    >
                      {item.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </nav>
        </div>
      </main>
    </div>
  );
}

function GuideSection({ section, number }: { section: Section; number: number }) {
  const { t } = useTranslation();
  const base = `guide.sections.${section.id}`;
  const steps = t(`${base}.steps`, { returnObjects: true }) as string[];
  const examples = section.id === "find"
    ? (t(`${base}.examples`, { returnObjects: true }) as { q: string; text: string }[])
    : [];

  return (
    <section id={section.id} className="scroll-mt-6">
      <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
        <span className="text-sm font-bold text-indigo-600">{number}.</span>
        {t(`${base}.title`)}
      </h2>
      <p className="text-sm text-gray-500 mt-1">{t(`${base}.intro`)}</p>
      <div className="mt-3 rounded-xl border border-gray-200 bg-white p-5">
        <ol className="space-y-2.5">
          {steps.map((step, i) => (
            <li key={i} className="flex gap-3 text-sm text-gray-700">
              <span className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600">
                {i + 1}
              </span>
              <span>{step}</span>
            </li>
          ))}
        </ol>

        {examples.length > 0 && (
          <div className="mt-4">
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              {t(`${base}.examplesTitle`)}
            </h3>
            <table className="text-sm">
              <tbody>
                {examples.map((ex) => (
                  <tr key={ex.q}>
                    <td className="py-1 pr-4">
                      <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-800">{ex.q}</code>
                    </td>
                    <td className="py-1 text-gray-600">{ex.text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {section.link && (
          <Link to={section.link.to} className="inline-block mt-4 text-sm font-medium text-indigo-700 hover:underline">
            {t("guide.open", { page: t(section.link.label) })}
          </Link>
        )}
      </div>
    </section>
  );
}
