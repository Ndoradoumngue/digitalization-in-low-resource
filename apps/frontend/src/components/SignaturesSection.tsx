import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { MentionableUser, SignatureRequest } from "@sdai/types";
import {
  useMentionable,
  useSignatureAction,
  useSignatureRequests,
  useSignatureVerification,
  useSigningStatus,
} from "../hooks/useCollaboration";
import { formatDateTime } from "../utils/lifecycle";

const inputClass =
  "h-8 w-full rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400";

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// "Signatures" tab (GED Phase 7): signature requests and their signers,
// signing (password re-entry), and the verification of the current version -
// PAdES check of every embedded signature next to SDAI's own evidence.
// Simple / advanced electronic signature - never "qualified".
export default function SignaturesSection({
  tableName,
  id,
  canRequest,
}: {
  tableName: string;
  id: string;
  canRequest: boolean;
}) {
  const { t } = useTranslation();
  const { data: status } = useSigningStatus();
  const { data: requests } = useSignatureRequests(tableName, id);
  const [asking, setAsking] = useState(false);
  const pending = (requests ?? []).find((r) => r.status === "pending");

  return (
    <div className="space-y-5">
      {status && !status.configured && (
        <p className="text-xs rounded-md bg-amber-50 text-amber-800 px-3 py-2">{t("signatures.notConfigured")}</p>
      )}
      {status?.configured && status.expired && (
        <p className="text-xs rounded-md bg-red-50 text-red-700 px-3 py-2">{t("signatures.certExpired")}</p>
      )}

      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{t("signatures.requests")}</h3>
        {canRequest && !pending && !asking && (
          <button onClick={() => setAsking(true)} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
            {t("signatures.ask")}
          </button>
        )}
      </div>
      {asking && <RequestForm tableName={tableName} id={id} onDone={() => setAsking(false)} />}
      {(requests ?? []).length === 0 && !asking && <p className="text-sm text-gray-400">{t("signatures.noRequests")}</p>}
      {(requests ?? []).map((r) => (
        <RequestCard key={r.id} request={r} tableName={tableName} id={id} />
      ))}

      <Verification tableName={tableName} id={id} />
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const { t } = useTranslation();
  const color =
    status === "completed" || status === "signed"
      ? "bg-green-100 text-green-800"
      : status === "pending"
        ? "bg-amber-100 text-amber-800"
        : "bg-gray-100 text-gray-600";
  return <span className={`px-1.5 py-0.5 rounded text-[11px] font-semibold ${color}`}>{t(`signatures.status.${status}`)}</span>;
}

function RequestCard({ request, tableName, id }: { request: SignatureRequest; tableName: string; id: string }) {
  const { t, i18n } = useTranslation();
  const action = useSignatureAction(tableName, id);
  const [mode, setMode] = useState<null | "sign" | "decline" | "cancel">(null);
  const [password, setPassword] = useState("");
  const [reason, setReason] = useState(request.reason);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  function close() {
    setMode(null);
    setPassword("");
    setNote("");
    setError(null);
  }

  function submit() {
    setError(null);
    const done = { onSuccess: close, onError: (e: unknown) => setError(errorText(e)) };
    if (mode === "sign") action.mutate({ kind: "sign", requestId: request.id, password, reason: reason.trim() || undefined }, done);
    if (mode === "decline") action.mutate({ kind: "decline", requestId: request.id, note: note.trim() || undefined }, done);
    if (mode === "cancel") action.mutate({ kind: "cancel", requestId: request.id, note: note.trim() || undefined }, done);
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 space-y-2">
      <div className="flex items-center gap-2">
        <StatusChip status={request.status} />
        <span className="text-sm font-medium text-gray-800">{request.reason}</span>
        <span className="flex-1" />
        <span className="text-xs text-gray-400">
          {t("signatures.askedBy", { name: request.requested_by_name ?? "?" })} · {formatDateTime(request.created_at, i18n.language)}
        </span>
      </div>
      {request.message && <p className="text-xs text-gray-600 whitespace-pre-wrap">{request.message}</p>}
      <ol className="space-y-1">
        {request.signers.map((s, i) => (
          <li key={s.user_id} className="flex items-center gap-2 text-sm">
            <span className="w-5 text-xs text-gray-400 text-right">{i + 1}.</span>
            <span className={s.is_next ? "font-semibold text-gray-900" : "text-gray-700"}>{s.name}</span>
            {s.title && <span className="text-xs text-gray-400">{s.title}</span>}
            <span className="flex-1" />
            <StatusChip status={s.status} />
            {s.signed_at && <span className="text-xs text-gray-400">{formatDateTime(s.signed_at, i18n.language)}</span>}
          </li>
        ))}
      </ol>
      {request.close_note && <p className="text-xs text-gray-500 italic">{request.close_note}</p>}

      {mode === null && (request.can_sign || request.can_cancel) && (
        <div className="flex justify-end gap-3 pt-1">
          {request.can_cancel && (
            <button onClick={() => setMode("cancel")} className="text-xs text-gray-500 hover:text-gray-800">{t("signatures.cancelRequest")}</button>
          )}
          {request.can_sign && (
            <>
              <button onClick={() => setMode("decline")} className="text-xs font-medium text-red-600 hover:text-red-800">{t("signatures.decline")}</button>
              <button onClick={() => setMode("sign")} className="px-3 py-1 rounded-md text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700">
                {t("signatures.sign")}
              </button>
            </>
          )}
        </div>
      )}

      {mode !== null && (
        <div className="rounded-md border border-gray-200 bg-gray-50 p-3 space-y-2">
          {mode === "sign" && (
            <>
              <p className="text-xs text-gray-600">{t("signatures.signHint")}</p>
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                maxLength={200}
                className={inputClass}
                aria-label={t("signatures.reason")}
                placeholder={t("signatures.reason")}
              />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className={inputClass}
                placeholder={t("signatures.password")}
                aria-label={t("signatures.password")}
                autoFocus
              />
              <p className="text-[11px] text-gray-400">{t("signatures.levelNote")}</p>
            </>
          )}
          {mode !== "sign" && (
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={1000}
              className={inputClass}
              placeholder={t("signatures.noteOptional")}
              autoFocus
            />
          )}
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <button onClick={close} className="px-3 py-1 rounded-md text-xs text-gray-600 hover:bg-gray-100">{t("common.cancel")}</button>
            <button
              onClick={submit}
              disabled={action.isPending || (mode === "sign" && !password)}
              className={`px-3 py-1 rounded-md text-xs font-semibold text-white disabled:opacity-50 ${
                mode === "sign" ? "bg-indigo-600 hover:bg-indigo-700" : "bg-red-600 hover:bg-red-700"
              }`}
            >
              {action.isPending && mode === "sign"
                ? t("signatures.signing")
                : mode === "sign"
                  ? t("signatures.sign")
                  : mode === "decline"
                    ? t("signatures.decline")
                    : t("signatures.cancelRequest")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function RequestForm({ tableName, id, onDone }: { tableName: string; id: string; onDone: () => void }) {
  const { t } = useTranslation();
  const action = useSignatureAction(tableName, id);
  const [q, setQ] = useState("");
  const [signers, setSigners] = useState<(MentionableUser & { title: string })[]>([]);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { data: candidates } = useMentionable(tableName, id, q, true);
  const available = (candidates ?? []).filter((c) => !signers.some((s) => s.id === c.id));

  function move(i: number, delta: number) {
    setSigners((list) => {
      const next = [...list];
      const [item] = next.splice(i, 1);
      next.splice(Math.max(0, Math.min(next.length, i + delta)), 0, item);
      return next;
    });
  }

  function submit() {
    setError(null);
    action.mutate(
      {
        kind: "request",
        body: {
          signers: signers.map((s) => ({ user_id: s.id, ...(s.title.trim() ? { title: s.title.trim() } : {}) })),
          reason: reason.trim(),
          message: message.trim() || undefined,
        },
      },
      { onSuccess: onDone, onError: (e) => setError(errorText(e)) },
    );
  }

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3 space-y-2">
      <p className="text-xs text-gray-600">{t("signatures.askHint")}</p>
      <input
        type="text"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        maxLength={200}
        className={inputClass}
        placeholder={t("signatures.reasonPlaceholder")}
      />
      {signers.length > 0 && (
        <ol className="space-y-1">
          {signers.map((s, i) => (
            <li key={s.id} className="flex items-center gap-2 text-sm">
              <span className="w-5 text-xs text-gray-400 text-right">{i + 1}.</span>
              <span className="font-medium text-gray-800 w-32 truncate">{s.name}</span>
              <input
                type="text"
                value={s.title}
                onChange={(e) =>
                  setSigners((list) => list.map((x) => (x.id === s.id ? { ...x, title: e.target.value } : x)))
                }
                maxLength={100}
                placeholder={t("signatures.titlePlaceholder")}
                className="h-7 flex-1 rounded border border-gray-300 px-2 text-xs"
              />
              <button onClick={() => move(i, -1)} disabled={i === 0} className="text-xs text-gray-500 disabled:opacity-30" aria-label={t("signatures.moveUp")}>↑</button>
              <button onClick={() => move(i, 1)} disabled={i === signers.length - 1} className="text-xs text-gray-500 disabled:opacity-30" aria-label={t("signatures.moveDown")}>↓</button>
              <button onClick={() => setSigners((list) => list.filter((x) => x.id !== s.id))} className="text-xs text-red-500" aria-label={t("signatures.remove")}>✕</button>
            </li>
          ))}
        </ol>
      )}
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className={inputClass}
        placeholder={t("signatures.addSigner")}
      />
      {available.length > 0 && (
        <ul className="max-h-32 overflow-y-auto rounded-md border border-gray-200 bg-white">
          {available.map((u) => (
            <li key={u.id}>
              <button
                onClick={() => setSigners((list) => [...list, { ...u, title: "" }])}
                className="w-full text-left px-3 py-1 text-sm hover:bg-indigo-50"
              >
                {u.name} <span className="text-xs text-gray-400">{u.email}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        rows={2}
        maxLength={2000}
        placeholder={t("signatures.messagePlaceholder")}
        className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
      />
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex justify-end gap-2">
        <button onClick={onDone} className="px-3 py-1 rounded-md text-xs text-gray-600 hover:bg-gray-100">{t("common.cancel")}</button>
        <button
          onClick={submit}
          disabled={action.isPending || signers.length === 0 || reason.trim().length < 2}
          className="px-3 py-1 rounded-md text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
        >
          {t("signatures.send")}
        </button>
      </div>
    </div>
  );
}

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs ${ok ? "text-green-700" : "text-red-700"}`}>
      {ok ? "✓" : "✗"} {label}
    </span>
  );
}

function Verification({ tableName, id }: { tableName: string; id: string }) {
  const { t, i18n } = useTranslation();
  const { data, isLoading } = useSignatureVerification(tableName, id);
  if (isLoading || !data) return null;
  const v = data.verification;
  const hasSignatures = (v?.signatures.length ?? 0) > 0;
  return (
    <div className="pt-4 border-t border-gray-200 space-y-3">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{t("signatures.verification")}</h3>
      {!hasSignatures && (
        <p className="text-sm text-gray-400">
          {v?.not_pdf ? t("signatures.notPdf") : t("signatures.noSignature", { version: v?.version_no ?? "-" })}
        </p>
      )}
      {v && hasSignatures && (
        <div
          className={`rounded-md px-3 py-2 text-sm font-medium ${
            v.all_ok && v.file_hash_ok !== false ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"
          }`}
        >
          {v.all_ok && v.file_hash_ok !== false
            ? t("signatures.allValid", { count: v.signatures.length, version: v.version_no })
            : t("signatures.invalid", { version: v.version_no })}
        </div>
      )}
      {v?.signatures.map((s) => (
        <div key={s.field} className="rounded-md border border-gray-200 px-3 py-2 space-y-1">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium text-gray-800">{s.signer_name ?? "?"}</span>
            {s.reason && <span className="text-xs text-gray-500">· {s.reason}</span>}
            <span className="flex-1" />
            {s.signed_at && <span className="text-xs text-gray-400">{formatDateTime(s.signed_at, i18n.language)}</span>}
          </div>
          <div className="flex flex-wrap gap-3">
            <Check ok={s.intact} label={t("signatures.intact")} />
            <Check ok={s.trusted} label={t("signatures.trusted")} />
            {s.evidence_matches !== undefined && <Check ok={!!s.evidence_matches} label={t("signatures.evidenceMatches")} />}
            {s.coverage && <span className="text-xs text-gray-500">{t(`signatures.coverage.${s.coverage}`, { defaultValue: s.coverage })}</span>}
          </div>
          {s.cert_subject && <p className="text-[11px] text-gray-400 truncate" title={s.cert_fingerprint}>{s.cert_subject}</p>}
        </div>
      ))}
      {v && hasSignatures && v.file_hash_ok === false && <p className="text-xs text-red-700">{t("signatures.hashMismatch")}</p>}

      {data.evidence.length > 0 && (
        <details className="text-xs text-gray-600">
          <summary className="cursor-pointer text-gray-500 hover:text-gray-800">{t("signatures.evidence")}</summary>
          <table className="mt-2 w-full">
            <thead className="text-gray-400 text-left">
              <tr>
                <th className="font-medium pr-2">{t("signatures.signer")}</th>
                <th className="font-medium pr-2">{t("signatures.when")}</th>
                <th className="font-medium pr-2">IP</th>
                <th className="font-medium pr-2">{t("signatures.versions")}</th>
                <th className="font-medium">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {data.evidence.map((e) => (
                <tr key={e.id} className="align-top">
                  <td className="pr-2">{e.signer_name}{e.signer_title ? ` (${e.signer_title})` : ""}</td>
                  <td className="pr-2">{formatDateTime(e.signed_at, i18n.language)}</td>
                  <td className="pr-2 font-mono">{e.ip_address ?? "-"}</td>
                  <td className="pr-2 font-mono">v{e.source_version_no}→v{e.version_no}</td>
                  <td className="font-mono" title={e.signed_file_hash}>{e.signed_file_hash.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-gray-400">{t("signatures.certificate")}: {data.evidence[0].cert_subject}</p>
          <p className="text-gray-400 font-mono break-all">{data.evidence[0].cert_fingerprint}</p>
        </details>
      )}
      {data.audit.length > 0 && (
        <details className="text-xs text-gray-600">
          <summary className="cursor-pointer text-gray-500 hover:text-gray-800">{t("signatures.auditTrail")}</summary>
          <ul className="mt-2 space-y-0.5">
            {data.audit.map((a, i) => (
              <li key={i}>
                {formatDateTime(a.created_at, i18n.language)} · {t(`audit.actions.${a.action}`, { defaultValue: a.action })} · {a.user_email ?? "-"}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
