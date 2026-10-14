import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import i18n from "../i18n/config";
import { formatDateTime } from "../utils/dates";
import type { TFunction } from "i18next";
import NavSidebar from "../components/NavSidebar";
import {
  useEmailOutbox,
  useEmailOutboxAction,
  useResolveSyncConflict,
  useRetrySyncFiles,
  useSyncConflicts,
  useSyncProblemFiles,
  useSyncStatus,
} from "../hooks/useSync";
import type { BackupRun, SyncAlert, SyncConflict, SyncResolution, SyncStatus } from "@sdai/types";

// ── Helpers ───────────────────────────────────────────────────────────────────

function when(value: string | null | undefined, never: string): string {
  return value ? formatDateTime(value, i18n.language) : never;
}

function megabytes(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(bytes > 10 * 1024 * 1024 ? 0 : 1);
}

function show(value: unknown): string {
  if (value === null || value === undefined) return "∅";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-white rounded-lg border border-gray-200 p-5">
      <h2 className="text-sm font-bold text-gray-900 mb-3">{title}</h2>
      {children}
    </section>
  );
}

function Stat({ label, value, tone = "gray" }: { label: string; value: React.ReactNode; tone?: "gray" | "red" | "amber" | "green" }) {
  const tones = {
    gray: "bg-gray-50 text-gray-800",
    red: "bg-red-50 text-red-800",
    amber: "bg-amber-50 text-amber-800",
    green: "bg-emerald-50 text-emerald-800",
  };
  return (
    <div className={`rounded-md px-3 py-2 ${tones[tone]}`}>
      <div className="text-[11px] opacity-80">{label}</div>
      <div className="text-sm font-semibold">{value}</div>
    </div>
  );
}

// ── Alerts ────────────────────────────────────────────────────────────────────

export function alertText(alert: SyncAlert, t: TFunction): string {
  const never = t("sync.never");
  return t(`sync.alerts.${alert.kind}`, {
    since: when(alert.since, never),
    hours: alert.hours,
    count: alert.count,
  });
}

function AlertsPanel({ alerts }: { alerts: SyncAlert[] }) {
  const { t } = useTranslation();
  if (alerts.length === 0) {
    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
        ✓ {t("sync.allGood")}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 space-y-1">
      {alerts.map((a) => (
        <p key={a.kind} className="text-sm text-red-800">⚠ {alertText(a, t)}</p>
      ))}
    </div>
  );
}

// ── Node + peers ──────────────────────────────────────────────────────────────

function NodePanel({ status }: { status: SyncStatus }) {
  const { t } = useTranslation();
  const never = t("sync.never");
  const { node, worker } = status;
  return (
    <Panel title={t("sync.node.heading")}>
      {node.join_pending && (
        <p className="mb-3 text-xs text-amber-800 bg-amber-50 rounded px-3 py-2">{t("sync.node.joinPending")}</p>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <Stat label={t("sync.node.id")} value={node.node_id} />
        <Stat label={t("sync.node.role")} value={t(node.node_role === "cloud" ? "nav.nodeCloud" : "nav.nodeLocal")} />
        <Stat
          label={t("sync.node.sync")}
          value={node.sync_enabled ? t("sync.node.enabled") : t("sync.node.disabled")}
          tone={node.sync_enabled ? "green" : "amber"}
        />
        {node.initiator ? (
          <>
            <Stat label={t("sync.node.peer")} value={node.peer_url ?? "-"} />
            <Stat label={t("sync.node.lastSuccess")} value={when(worker.last_success_at, never)} />
            <Stat
              label={t("sync.node.failures")}
              value={worker.failures}
              tone={worker.failures > 0 ? "red" : "gray"}
            />
          </>
        ) : (
          node.sync_enabled && <Stat label={t("sync.node.passive")} value={t("sync.node.passiveHint")} />
        )}
      </div>
      {worker.last_error && worker.failures > 0 && (
        <p className="mt-3 text-xs text-red-700 font-mono break-all">
          {when(worker.last_error_at, never)} - {worker.last_error}
        </p>
      )}
    </Panel>
  );
}

function PeersPanel({ status }: { status: SyncStatus }) {
  const { t } = useTranslation();
  const never = t("sync.never");
  if (status.peers.length === 0) {
    return (
      <Panel title={t("sync.peers.heading")}>
        <p className="text-xs text-gray-500">{t("sync.peers.none")}</p>
      </Panel>
    );
  }
  return (
    <Panel title={t("sync.peers.heading")}>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-gray-500">
            <th className="py-1 pr-2">{t("sync.peers.node")}</th>
            <th className="py-1 pr-2">{t("sync.peers.lastContact")}</th>
            <th className="py-1 pr-2">{t("sync.peers.lastPush")}</th>
            <th className="py-1 pr-2">{t("sync.peers.lastPull")}</th>
            <th className="py-1 pr-2 text-right">{t("sync.peers.pending")}</th>
          </tr>
        </thead>
        <tbody>
          {status.peers.map((p) => (
            <tr key={p.peer_node} className="border-t border-gray-100">
              <td className="py-1.5 pr-2 font-medium">{p.peer_node}</td>
              <td className="py-1.5 pr-2">{when(p.last_contact_at, never)}</td>
              <td className="py-1.5 pr-2">{when(p.last_sent_at, never)}</td>
              <td className="py-1.5 pr-2">{when(p.last_received_at, never)}</td>
              <td className="py-1.5 pr-2 text-right">{p.pending_changes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// ── Files ─────────────────────────────────────────────────────────────────────

function FilesPanel({ status }: { status: SyncStatus }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const problems = useSyncProblemFiles();
  const retry = useRetrySyncFiles();
  const f = status.files;
  return (
    <Panel title={t("sync.files.heading")}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label={t("sync.files.pending")} value={f.pending} tone={f.pending ? "amber" : "gray"} />
        <Stat label={t("sync.files.pendingMb")} value={`${megabytes(f.pending_bytes)} Mo`} />
        <Stat label={t("sync.files.waiting")} value={f.waiting} tone={f.waiting ? "amber" : "gray"} />
        <Stat label={t("sync.files.failed")} value={f.failed} tone={f.failed ? "red" : "gray"} />
      </div>
      {!status.node.initiator && status.node.sync_enabled && (
        <p className="mt-2 text-[11px] text-gray-500">{t("sync.files.passiveHint")}</p>
      )}
      {(f.failed > 0 || f.waiting > 0) && (
        <div className="mt-3 flex items-center gap-3">
          <button onClick={() => setOpen((o) => !o)} className="text-xs text-indigo-600 hover:underline">
            {open ? t("sync.files.hide") : t("sync.files.show")}
          </button>
          <button
            onClick={() => retry.mutate()}
            disabled={retry.isPending}
            className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-40"
          >
            {t("sync.files.retry")}
          </button>
        </div>
      )}
      {open && (
        <ul className="mt-2 space-y-1 max-h-64 overflow-y-auto">
          {(problems.data?.items ?? []).map((file) => (
            <li key={`${file.path}-${file.direction}`} className="text-[11px] font-mono text-gray-700 break-all">
              <span className={file.status === "failed" ? "text-red-700" : "text-amber-700"}>
                [{t(`sync.files.status.${file.status}`)} · {t(`sync.files.direction.${file.direction}`)}]
              </span>{" "}
              {file.path}
              {file.last_error && <span className="text-gray-500"> - {file.last_error}</span>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ── E-mails (GED Phase 7) ─────────────────────────────────────────────────────

function EmailsPanel() {
  const { t } = useTranslation();
  const { data } = useEmailOutbox();
  const action = useEmailOutboxAction();
  if (!data) return null;
  const failed = data.items.filter((e) => e.status === "failed");
  return (
    <Panel title={t("sync.emails.heading")}>
      {!data.email_enabled && <p className="mb-3 text-xs text-amber-700">{t("sync.emails.disabled")}</p>}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label={t("sync.emails.pending")} value={data.counts.pending + data.counts.sending} tone={data.counts.pending ? "amber" : "gray"} />
        <Stat label={t("sync.emails.failed")} value={data.counts.failed} tone={data.counts.failed ? "red" : "gray"} />
        <Stat label={t("sync.emails.sent")} value={data.counts.sent} tone="green" />
      </div>
      <p className="mt-2 text-[11px] text-gray-500">{t("sync.emails.hint")}</p>
      {failed.length > 0 && (
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={() => action.mutate({ kind: "retry" })}
            disabled={action.isPending}
            className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-40"
          >
            {t("sync.emails.retryAll")}
          </button>
          <button
            onClick={() => action.mutate({ kind: "discard" })}
            disabled={action.isPending}
            className="text-xs text-red-600 hover:underline disabled:opacity-40"
          >
            {t("sync.emails.discardAll")}
          </button>
        </div>
      )}
      {data.items.length > 0 && (
        <ul className="mt-3 divide-y divide-gray-100 max-h-80 overflow-y-auto">
          {data.items.map((e) => (
            <li key={e.id} className="py-2 text-xs">
              <div className="flex items-center gap-2">
                <span className={`px-1.5 py-0.5 rounded font-semibold ${e.status === "failed" ? "bg-red-100 text-red-700" : "bg-amber-100 text-amber-800"}`}>
                  {t(`sync.emails.status.${e.status}`)}
                </span>
                <span className="font-medium text-gray-800">{e.to_email}</span>
                <span className="text-gray-500 truncate">{e.subject}</span>
                <span className="flex-1" />
                <span className="text-gray-400">{when(e.created_at, "-")}</span>
                {e.status === "failed" && (
                  <button
                    onClick={() => action.mutate({ kind: "retry", ids: [e.id] })}
                    disabled={action.isPending}
                    className="text-indigo-600 hover:underline"
                  >
                    {t("sync.emails.retry")}
                  </button>
                )}
              </div>
              {e.last_error && (
                <p className="mt-0.5 text-gray-500 font-mono break-all">
                  {t("sync.emails.attempts", { count: e.attempts })} · {e.last_error}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {data.items.length === 0 && <p className="mt-3 text-xs text-gray-400">{t("sync.emails.none")}</p>}
    </Panel>
  );
}

// ── Conflicts ─────────────────────────────────────────────────────────────────

function ConflictRow({ c }: { c: SyncConflict }) {
  const { t } = useTranslation();
  const resolve = useResolveSyncConflict();
  const [manual, setManual] = useState<string | null>(null);
  const isField = c.kind === "field";
  const act = (action: SyncResolution, value?: unknown) => resolve.mutate({ id: c.id, action, value });

  return (
    <li className="border border-gray-200 rounded-md p-3 text-xs space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`px-1.5 py-0.5 rounded font-semibold ${isField ? "bg-orange-100 text-orange-800" : "bg-red-100 text-red-800"}`}>
          {t(`sync.conflicts.kind.${c.kind}`, c.kind)}
        </span>
        {c.document_id && c.table_name ? (
          <Link to={`/documents/${c.table_name}/${c.document_id}`} className="text-indigo-600 hover:underline font-mono">
            {c.table_name}
          </Link>
        ) : (
          <span className="font-mono text-gray-600">{c.table_name ?? "-"}</span>
        )}
        {c.field && <span className="font-mono font-semibold">{c.field}</span>}
        {c.occurrences > 1 && <span className="text-gray-500">×{c.occurrences}</span>}
        <span className="ml-auto text-gray-400">{when(c.updated_at ?? c.created_at, "")}</span>
      </div>

      {isField ? (
        <div className="grid grid-cols-2 gap-2">
          <div className={`rounded px-2 py-1 ${c.winner === "local" ? "bg-emerald-50" : "bg-gray-50"}`}>
            <div className="text-[10px] text-gray-500">
              {t("sync.conflicts.localValue")} {c.winner === "local" && `· ${t("sync.conflicts.kept")}`}
            </div>
            <div className="font-mono break-all">{show(c.local_value)}</div>
          </div>
          <div className={`rounded px-2 py-1 ${c.winner === "remote" ? "bg-emerald-50" : "bg-gray-50"}`}>
            <div className="text-[10px] text-gray-500">
              {t("sync.conflicts.remoteValue", { node: c.remote_node ?? "?" })}{" "}
              {c.winner === "remote" && `· ${t("sync.conflicts.kept")}`}
            </div>
            <div className="font-mono break-all">{show(c.remote_value)}</div>
          </div>
        </div>
      ) : (
        c.message && <p className="font-mono text-red-700 break-all">{c.message}</p>
      )}

      {c.status === "open" ? (
        <div className="flex flex-wrap items-center gap-2">
          {isField ? (
            <>
              <button onClick={() => act("keep_local")} disabled={resolve.isPending} className="px-2 py-1 rounded border border-gray-300 hover:bg-gray-50">
                {t("sync.conflicts.keepLocal")}
              </button>
              <button onClick={() => act("keep_remote")} disabled={resolve.isPending} className="px-2 py-1 rounded border border-gray-300 hover:bg-gray-50">
                {t("sync.conflicts.keepRemote")}
              </button>
              {manual === null ? (
                <button onClick={() => setManual(typeof c.local_value === "string" ? c.local_value : "")} className="px-2 py-1 rounded border border-gray-300 hover:bg-gray-50">
                  {t("sync.conflicts.manual")}
                </button>
              ) : (
                <span className="flex items-center gap-1">
                  <input
                    value={manual}
                    onChange={(e) => setManual(e.target.value)}
                    className="border border-gray-300 rounded px-2 py-1 text-xs w-48"
                    aria-label={t("sync.conflicts.manualValue")}
                  />
                  <button onClick={() => act("manual", manual)} disabled={resolve.isPending} className="px-2 py-1 rounded bg-indigo-600 text-white">
                    {t("common.save")}
                  </button>
                </span>
              )}
            </>
          ) : (
            <button onClick={() => act("retry")} disabled={resolve.isPending} className="px-2 py-1 rounded border border-gray-300 hover:bg-gray-50">
              {t("sync.conflicts.retry")}
            </button>
          )}
          <button onClick={() => act("dismiss")} disabled={resolve.isPending} className="px-2 py-1 rounded text-gray-500 hover:bg-gray-50">
            {t("sync.conflicts.dismiss")}
          </button>
          {resolve.isError && <span className="text-red-600">{(resolve.error as Error).message}</span>}
        </div>
      ) : (
        <p className="text-gray-500">
          {t(`sync.conflicts.resolution.${c.resolution}`, c.resolution ?? "")} · {when(c.resolved_at, "")}
        </p>
      )}
    </li>
  );
}

function ConflictsPanel() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const { data, isLoading } = useSyncConflicts(status);
  return (
    <Panel title={t("sync.conflicts.heading")}>
      <p className="text-xs text-gray-500 mb-3">{t("sync.conflicts.help")}</p>
      <div className="flex gap-2 mb-3">
        {(["open", "resolved"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setStatus(s)}
            className={`px-3 py-1 rounded-full text-xs font-semibold ${status === s ? "bg-indigo-600 text-white" : "bg-gray-100 text-gray-600"}`}
          >
            {t(`sync.conflicts.tab.${s}`)}
          </button>
        ))}
      </div>
      {isLoading ? (
        <p className="text-xs text-gray-500">{t("common.loading")}</p>
      ) : (data?.items.length ?? 0) === 0 ? (
        <p className="text-xs text-gray-500">{t("sync.conflicts.none")}</p>
      ) : (
        <ul className="space-y-2">
          {data!.items.map((c) => (
            <ConflictRow key={c.id} c={c} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ── Backups ───────────────────────────────────────────────────────────────────

function BackupsPanel({ backups, lastSuccess }: { backups: BackupRun[]; lastSuccess: string | null }) {
  const { t } = useTranslation();
  const never = t("sync.never");
  return (
    <Panel title={t("sync.backups.heading")}>
      <p className="text-xs text-gray-500 mb-3">
        {t("sync.backups.lastSuccess", { when: when(lastSuccess, never) })}
      </p>
      {backups.length === 0 ? (
        <p className="text-xs text-gray-500">{t("sync.backups.none")}</p>
      ) : (
        <ul className="space-y-2">
          {backups.map((b) => (
            <li key={b.target ?? "-"} className="text-xs border border-gray-100 rounded px-3 py-2">
              <div className="flex items-center gap-2">
                <span
                  className={`px-1.5 py-0.5 rounded font-semibold ${
                    b.status === "success" ? "bg-emerald-100 text-emerald-800" : b.status === "failed" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800"
                  }`}
                >
                  {t(`sync.backups.status.${b.status}`)}
                </span>
                <span className="font-medium">{b.target ? t(`sync.backups.target.${b.target}`, b.target) : "-"}</span>
                <span className="ml-auto text-gray-400">{when(b.finished_at, never)}</span>
              </div>
              {b.snapshot_id && (
                <p className="mt-1 font-mono text-gray-500">
                  {b.snapshot_id.slice(0, 12)} · +{megabytes(b.bytes_added ?? 0)} Mo
                </p>
              )}
              {b.message && <p className="mt-1 text-red-700 break-all">{b.message}</p>}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-gray-500">{t("sync.backups.help")}</p>
    </Panel>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SyncAdminPage() {
  const { t } = useTranslation();
  const { data: status, isLoading, error } = useSyncStatus();

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
          <h1 className="text-lg font-bold text-gray-900">{t("sync.header")}</h1>
          <p className="text-xs text-gray-500 mt-0.5">{t("sync.subtitle")}</p>
        </header>
        <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-4xl">
          {isLoading && <p className="text-sm text-gray-500">{t("common.loading")}</p>}
          {error && <p className="text-sm text-red-600">{(error as Error).message}</p>}
          {status && (
            <>
              <AlertsPanel alerts={status.alerts} />
              <NodePanel status={status} />
              <PeersPanel status={status} />
              <FilesPanel status={status} />
              <ConflictsPanel />
              <BackupsPanel backups={status.backups} lastSuccess={status.last_backup_success} />
              <EmailsPanel />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
