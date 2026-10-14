import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { destructionPvUrl } from "@sdai/api-client";
import type { DestructionRecord } from "@sdai/types";
import NavSidebar from "../components/NavSidebar";
import { useAuth } from "../context/AuthContext";
import { useConfirm } from "../context/ConfirmContext";
import {
  useDecideDestruction,
  useDestruction,
  useDestructions,
  useEligibleDocuments,
  useProposeDestruction,
} from "../hooks/useLifecycle";
import { useDownloadFile } from "../hooks/useSharing";
import { formatDate, formatDateTime } from "../utils/lifecycle";

const STATUS_STYLE: Record<DestructionRecord["status"], string> = {
  proposed: "bg-amber-100 text-amber-800",
  approved: "bg-gray-800 text-white",
  rejected: "bg-gray-100 text-gray-600",
  cancelled: "bg-gray-100 text-gray-500",
};

const key = (table: string, id: string) => `${table}/${id}`;

// "Éliminations": documents whose retention has ended, destruction proposals
// (two different people: an archivist proposes, another admin approves) and
// the records with their procès-verbal.
export default function DestructionsPage() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const confirm = useConfirm();
  const { data: eligible, isLoading } = useEligibleDocuments();
  const { data: records } = useDestructions();
  const propose = useProposeDestruction();
  const decide = useDecideDestruction();
  const download = useDownloadFile();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [comment, setComment] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectable = (eligible ?? []).filter((d) => !d.legal_hold && !d.pending_destruction_id);

  function toggle(k: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  async function onPropose() {
    const docs = selectable.filter((d) => selected.has(key(d.table_name, d.id)));
    if (!docs.length) return;
    if (!(await confirm({ message: t("destructions.proposeConfirm", { count: docs.length }), confirmLabel: t("destructions.propose") }))) return;
    setError(null);
    propose.mutate(
      { documents: docs.map((d) => ({ table_name: d.table_name, id: d.id })), comment: comment.trim() || undefined },
      { onSuccess: () => { setSelected(new Set()); setComment(""); }, onError: (e) => setError((e as Error).message) },
    );
  }

  async function onDecide(record: DestructionRecord, decision: "approve" | "reject" | "cancel") {
    const message =
      decision === "approve"
        ? t("destructions.approveConfirm", { count: record.document_count, number: record.number })
        : decision === "reject"
          ? t("destructions.rejectConfirm", { number: record.number })
          : t("destructions.cancelConfirm", { number: record.number });
    if (!(await confirm({ message, danger: decision === "approve", confirmLabel: t(`destructions.${decision}`) }))) return;
    setError(null);
    decide.mutate({ recordId: record.id, decision }, { onError: (e) => setError((e as Error).message) });
  }

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-gray-200 px-6 py-3 flex-shrink-0">
          <h1 className="text-base font-bold text-indigo-700 tracking-tight">{t("destructions.header")}</h1>
          <p className="text-xs text-gray-500 mt-0.5">{t("destructions.intro")}</p>
        </header>
        <div className="flex-1 overflow-auto p-6 space-y-8">
          {error && <p role="alert" className="text-sm text-red-600">{error}</p>}

          <section className="max-w-5xl">
            <h2 className="text-sm font-bold text-gray-800 mb-2">{t("destructions.eligibleHeader")}</h2>
            {isLoading ? (
              <p className="text-sm text-gray-400">{t("destructions.loading")}</p>
            ) : (eligible ?? []).length === 0 ? (
              <p className="text-sm text-gray-400">{t("destructions.noneEligible")}</p>
            ) : (
              <>
                <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                      <tr>
                        <th className="px-3 py-2 w-8">
                          <input
                            type="checkbox"
                            aria-label={t("destructions.selectAll")}
                            checked={selectable.length > 0 && selectable.every((d) => selected.has(key(d.table_name, d.id)))}
                            onChange={(e) =>
                              setSelected(e.target.checked ? new Set(selectable.map((d) => key(d.table_name, d.id))) : new Set())
                            }
                          />
                        </th>
                        <th className="text-left px-3 py-2">{t("trash.document")}</th>
                        <th className="text-left px-3 py-2">{t("lifecycle.retentionUntil")}</th>
                        <th className="text-left px-3 py-2">{t("lifecycle.rule")}</th>
                        <th className="text-left px-3 py-2" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {(eligible ?? []).map((d) => {
                        const k = key(d.table_name, d.id);
                        const blocked = d.legal_hold || !!d.pending_destruction_id;
                        return (
                          <tr key={k} className={blocked ? "bg-gray-50" : ""}>
                            <td className="px-3 py-2">
                              <input
                                type="checkbox"
                                disabled={blocked}
                                checked={selected.has(k)}
                                onChange={() => toggle(k)}
                                aria-label={d.record_id ?? d.id}
                              />
                            </td>
                            <td className="px-3 py-2">
                              <Link to={`/documents/${d.table_name}/${d.id}`} className="hover:underline">
                                <span className="font-mono text-xs text-gray-600 mr-2">{d.record_id}</span>
                                <span className="text-gray-800">{d.display ?? d.document_type}</span>
                              </Link>
                            </td>
                            <td className="px-3 py-2 text-xs text-gray-600">{formatDate(d.retention_until, i18n.language)}</td>
                            <td className="px-3 py-2 text-xs text-gray-600">
                              {d.rule_name ?? t("lifecycle.manualDate")}
                              {d.final_action === "review" && (
                                <span className="ml-1 text-amber-700">({t("retention.finalAction.review")})</span>
                              )}
                            </td>
                            <td className="px-3 py-2 text-xs">
                              {d.legal_hold && <span className="text-purple-700">{t("lifecycle.legalHold")}</span>}
                              {d.pending_destruction_id && <span className="text-amber-700">{t("destructions.alreadyProposed")}</span>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <input
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    placeholder={t("destructions.commentPlaceholder")}
                    className="h-8 flex-1 min-w-[240px] rounded-md border border-gray-300 px-2 text-sm"
                    maxLength={2000}
                  />
                  <button
                    onClick={onPropose}
                    disabled={selected.size === 0 || propose.isPending}
                    className="px-3 py-1.5 rounded-md text-xs font-semibold bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                  >
                    {t("destructions.proposeCount", { count: selected.size })}
                  </button>
                </div>
              </>
            )}
          </section>

          <section className="max-w-5xl">
            <h2 className="text-sm font-bold text-gray-800 mb-2">{t("destructions.recordsHeader")}</h2>
            {(records ?? []).length === 0 ? (
              <p className="text-sm text-gray-400">{t("destructions.noRecords")}</p>
            ) : (
              <ul className="space-y-2">
                {(records ?? []).map((r) => {
                  const mine = r.proposed_by_id === user?.id;
                  return (
                    <li key={r.id} className="bg-white rounded-lg border border-gray-200 p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`inline-flex px-1.5 py-0.5 rounded text-[11px] font-semibold ${STATUS_STYLE[r.status]}`}>
                          {t(`destructions.status.${r.status}`)}
                        </span>
                        <span className="font-mono text-sm text-gray-800">{r.number}</span>
                        <span className="text-sm text-gray-600">
                          {t(`destructions.kind.${r.kind}`)} · {t("destructions.documents", { count: r.document_count })}
                        </span>
                        <span className="ml-auto text-xs text-gray-400">{formatDateTime(r.proposed_at, i18n.language)}</span>
                      </div>
                      <div className="mt-1 text-xs text-gray-500 space-x-3">
                        <span>{t("destructions.proposedBy", { who: r.proposed_by ?? t("destructions.system") })}</span>
                        {r.decided_at && (
                          <span>
                            {t("destructions.decidedBy", {
                              who: r.decided_by ?? t("destructions.system"),
                              date: formatDateTime(r.decided_at, i18n.language),
                            })}
                          </span>
                        )}
                      </div>
                      {r.comment && <p className="mt-1 text-xs text-gray-700">{r.comment}</p>}
                      {r.decision_note && <p className="mt-0.5 text-xs text-gray-500">↳ {r.decision_note}</p>}
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          onClick={() => setOpen(open === r.id ? null : r.id)}
                          className="px-3 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50"
                        >
                          {open === r.id ? t("destructions.hideDocuments") : t("destructions.showDocuments")}
                        </button>
                        {r.status === "proposed" && user?.role === "admin" && !mine && (
                          <>
                            <button
                              onClick={() => onDecide(r, "approve")}
                              disabled={decide.isPending}
                              className="px-3 py-1 rounded-md text-xs font-semibold bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                            >
                              {t("destructions.approve")}
                            </button>
                            <button
                              onClick={() => onDecide(r, "reject")}
                              disabled={decide.isPending}
                              className="px-3 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                            >
                              {t("destructions.reject")}
                            </button>
                          </>
                        )}
                        {r.status === "proposed" && mine && (
                          <>
                            <span className="self-center text-xs text-gray-500">{t("destructions.waitingOtherAdmin")}</span>
                            <button
                              onClick={() => onDecide(r, "cancel")}
                              disabled={decide.isPending}
                              className="px-3 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                            >
                              {t("destructions.cancel")}
                            </button>
                          </>
                        )}
                        {r.executed_at && (
                          <button
                            onClick={() => download.mutate(destructionPvUrl(r.id))}
                            disabled={download.isPending}
                            className="px-3 py-1 rounded-md text-xs font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            {t("destructions.downloadPv")}
                          </button>
                        )}
                      </div>
                      {open === r.id && <RecordDocuments recordId={r.id} />}
                    </li>
                  );
                })}
              </ul>
            )}
            {download.isError && <p className="mt-2 text-xs text-red-600">{(download.error as Error).message}</p>}
          </section>
        </div>
      </div>
    </div>
  );
}

function RecordDocuments({ recordId }: { recordId: string }) {
  const { t, i18n } = useTranslation();
  const { data } = useDestruction(recordId);
  if (!data?.documents) return <p className="mt-2 text-xs text-gray-400">{t("destructions.loading")}</p>;
  return (
    <table className="mt-3 w-full text-xs">
      <thead className="text-gray-500">
        <tr>
          <th className="text-left py-1">{t("destructions.reference")}</th>
          <th className="text-left py-1">{t("destructions.type")}</th>
          <th className="text-left py-1">{t("destructions.dates")}</th>
          <th className="text-left py-1">{t("destructions.fingerprints")}</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-100 align-top">
        {data.documents.map((d) => (
          <tr key={`${d.table_name}/${d.id}`}>
            <td className="py-1 font-mono">{d.record_id ?? d.id}</td>
            <td className="py-1">{d.document_type}</td>
            <td className="py-1 text-gray-600">
              {formatDate(d.ingested_at, i18n.language)}
              {d.retention_until && <> → {formatDate(d.retention_until, i18n.language)}</>}
            </td>
            <td className="py-1 font-mono text-[10px] text-gray-500 break-all">
              {[d.original_hash, ...(d.versions ?? []).map((v) => v.file_hash)]
                .filter(Boolean)
                .filter((h, i, all) => all.indexOf(h) === i)
                .map((h) => (
                  <div key={h as string}>{h}</div>
                ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
