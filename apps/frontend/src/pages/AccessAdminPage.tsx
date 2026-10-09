import { useState } from "react";
import { useTranslation } from "react-i18next";
import NavSidebar from "../components/NavSidebar";
import { useConfirm } from "../context/ConfirmContext";
import {
  useGroups,
  useCreateGroup,
  useDeleteGroup,
  useAdminUsers,
  useUpdateAdminUser,
  useAddUserToGroup,
  useRemoveUserFromGroup,
  useRunIntegrityCheck,
  useExportArchive,
} from "../hooks/useAdmin";
import { useSeries, useCreateSeries, useDeleteSeries } from "../hooks/useSeries";
import {
  CONFIDENTIALITY_LEVELS,
  type AccessGrant,
  type AdminUser,
  type Confidentiality,
  type Group,
  type PermissionLevel,
  type Series,
} from "@sdai/types";
import type { UpdateUserBody } from "@sdai/api-client";
import {
  useCreateScopedGrant,
  useDeleteGrant,
  useGrantees,
  useScopedGrants,
  useUpdateAccessGrant,
} from "../hooks/useAccess";
import { useDbTypes } from "../hooks/useDocumentsDb";
import { PermissionSelect } from "../components/AccessSection";

// ── Archive export panel ─────────────────────────────────────────────────────

function ExportArchivePanel() {
  const { t } = useTranslation();
  const [format, setFormat] = useState<"json" | "sql">("json");
  const exportMutation = useExportArchive();

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <h2 className="text-sm font-bold text-gray-900 mb-1">{t("accessAdmin.export.heading")}</h2>
      <p className="text-xs text-gray-500 mb-3">
        {t("accessAdmin.export.descriptionPrefix")}
        <code className="font-mono">documents/</code>
        {t("accessAdmin.export.descriptionSuffix")}
      </p>

      <div className="flex items-center gap-2">
        <select
          value={format}
          onChange={(e) => setFormat(e.target.value as "json" | "sql")}
          className="h-9 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <option value="json">JSON</option>
          <option value="sql">SQL</option>
        </select>
        <button
          onClick={() => exportMutation.mutate(format)}
          disabled={exportMutation.isPending}
          className="px-3 py-2 rounded-md text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {exportMutation.isPending ? t("accessAdmin.export.preparing") : t("accessAdmin.export.download")}
        </button>
      </div>

      {exportMutation.isError && (
        <p className="text-xs text-red-600 mt-3">{(exportMutation.error as Error).message}</p>
      )}
      {exportMutation.isSuccess && !exportMutation.isPending && (
        <p className="text-xs text-emerald-600 mt-3">{t("accessAdmin.export.started")}</p>
      )}
    </div>
  );
}

// ── Fixity / integrity check panel ──────────────────────────────────────────

function IntegrityCheckPanel() {
  const { t } = useTranslation();
  const runMutation = useRunIntegrityCheck();
  const result = runMutation.data;

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <h2 className="text-sm font-bold text-gray-900 mb-1">{t("accessAdmin.integrity.heading")}</h2>
      <p className="text-xs text-gray-500 mb-3">
        {t("accessAdmin.integrity.description")}
      </p>

      <button
        onClick={() => runMutation.mutate()}
        disabled={runMutation.isPending}
        className="px-3 py-2 rounded-md text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
      >
        {runMutation.isPending ? t("accessAdmin.integrity.running") : t("accessAdmin.integrity.run")}
      </button>

      {runMutation.isError && (
        <p className="text-xs text-red-600 mt-3">{(runMutation.error as Error).message}</p>
      )}

      {result && (
        <div className="mt-4 grid grid-cols-4 gap-3 text-center">
          <div className="rounded-md bg-gray-50 py-2">
            <div className="text-lg font-bold text-gray-800">{result.checked}</div>
            <div className="text-[11px] text-gray-500">{t("accessAdmin.integrity.checked")}</div>
          </div>
          <div className="rounded-md bg-emerald-50 py-2">
            <div className="text-lg font-bold text-emerald-700">{result.ok}</div>
            <div className="text-[11px] text-emerald-600">{t("accessAdmin.integrity.ok")}</div>
          </div>
          <div className="rounded-md bg-amber-50 py-2">
            <div className="text-lg font-bold text-amber-700">{result.mismatched}</div>
            <div className="text-[11px] text-amber-600">{t("accessAdmin.integrity.mismatched")}</div>
          </div>
          <div className="rounded-md bg-red-50 py-2">
            <div className="text-lg font-bold text-red-700">{result.missing}</div>
            <div className="text-[11px] text-red-600">{t("accessAdmin.integrity.missing")}</div>
          </div>
        </div>
      )}
      {result && (result.mismatched > 0 || result.missing > 0) && (
        <p className="text-xs text-gray-500 mt-3">
          {t("accessAdmin.integrity.failuresNotePrefix")}
          <code className="font-mono">integrity_check_failed</code>
          {t("accessAdmin.integrity.failuresNoteSuffix")}
        </p>
      )}
    </div>
  );
}

// ── Series panel ──────────────────────────────────────────────────────────────

function SeriesPanel({ series, isLoading }: { series: Series[]; isLoading: boolean }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const [name, setName]               = useState("");
  const [description, setDescription] = useState("");
  const createMutation = useCreateSeries();
  const deleteMutation = useDeleteSeries();
  const [deletingId, setDeletingId] = useState<string | null>(null);

  function submit() {
    if (!name.trim()) return;
    createMutation.mutate(
      { name: name.trim(), description: description.trim() || undefined },
      { onSuccess: () => { setName(""); setDescription(""); } },
    );
  }

  async function remove(seriesId: string) {
    if (!(await confirm({ message: t("accessAdmin.series.confirmDelete"), danger: true }))) return;
    setDeletingId(seriesId);
    deleteMutation.mutate(seriesId, { onSettled: () => setDeletingId(null) });
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <h2 className="text-sm font-bold text-gray-900 mb-3">{t("accessAdmin.series.heading")}</h2>

      <div className="flex items-center gap-2 mb-4">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("accessAdmin.series.namePlaceholder")}
          className="flex-1 h-9 rounded-md border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t("accessAdmin.series.descriptionPlaceholder")}
          className="flex-1 h-9 rounded-md border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <button
          onClick={submit}
          disabled={!name.trim() || createMutation.isPending}
          className="px-3 py-2 rounded-md text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {createMutation.isPending ? t("accessAdmin.series.creating") : t("accessAdmin.series.create")}
        </button>
      </div>
      {createMutation.isError && (
        <p className="text-xs text-red-600 mb-3">{(createMutation.error as Error).message}</p>
      )}

      {isLoading ? (
        <p className="text-sm text-gray-400">{t("accessAdmin.series.loading")}</p>
      ) : series.length === 0 ? (
        <p className="text-sm text-gray-400">{t("accessAdmin.series.none")}</p>
      ) : (
        <div className="divide-y divide-gray-100">
          {series.map((s) => (
            <div key={s.id} className="flex items-center justify-between gap-2 py-2">
              <div>
                <span className="text-sm font-medium text-gray-800">{s.name}</span>
                {s.description && (
                  <span className="text-xs text-gray-400 ml-2">{s.description}</span>
                )}
              </div>
              <button
                onClick={() => remove(s.id)}
                disabled={deletingId === s.id}
                className="text-xs text-gray-400 hover:text-red-600 disabled:opacity-40 transition-colors"
              >
                {t("accessAdmin.series.delete")}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Groups panel ──────────────────────────────────────────────────────────────

function GroupsPanel({ groups, isLoading }: { groups: Group[]; isLoading: boolean }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const [name, setName] = useState("");
  const createMutation = useCreateGroup();
  const deleteMutation = useDeleteGroup();
  const [deletingId, setDeletingId] = useState<string | null>(null);

  function submit() {
    if (!name.trim()) return;
    createMutation.mutate(name.trim(), { onSuccess: () => setName("") });
  }

  async function remove(groupId: string) {
    if (!(await confirm({ message: t("accessAdmin.groups.confirmDelete"), danger: true }))) return;
    setDeletingId(groupId);
    deleteMutation.mutate(groupId, { onSettled: () => setDeletingId(null) });
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <h2 className="text-sm font-bold text-gray-900 mb-3">{t("accessAdmin.groups.heading")}</h2>

      <div className="flex items-center gap-2 mb-4">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder={t("accessAdmin.groups.namePlaceholder")}
          className="flex-1 h-9 rounded-md border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <button
          onClick={submit}
          disabled={!name.trim() || createMutation.isPending}
          className="px-3 py-2 rounded-md text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {createMutation.isPending ? t("accessAdmin.groups.creating") : t("accessAdmin.groups.create")}
        </button>
      </div>
      {createMutation.isError && (
        <p className="text-xs text-red-600 mb-3">{(createMutation.error as Error).message}</p>
      )}

      {isLoading ? (
        <p className="text-sm text-gray-400">{t("accessAdmin.groups.loading")}</p>
      ) : groups.length === 0 ? (
        <p className="text-sm text-gray-400">{t("accessAdmin.groups.none")}</p>
      ) : (
        <div className="divide-y divide-gray-100">
          {groups.map((g) => (
            <div key={g.id} className="flex items-center justify-between gap-2 py-2">
              <div>
                <span className="text-sm font-medium text-gray-800">{g.name}</span>
                <span className="text-xs text-gray-400 ml-2">
                  {t(g.member_count === 1 ? "accessAdmin.groups.member_one" : "accessAdmin.groups.member_other", { count: g.member_count })}
                </span>
              </div>
              <button
                onClick={() => remove(g.id)}
                disabled={deletingId === g.id}
                className="text-xs text-gray-400 hover:text-red-600 disabled:opacity-40 transition-colors"
              >
                {t("accessAdmin.groups.delete")}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Users panel ───────────────────────────────────────────────────────────────

const USER_FLAGS = [
  ["can_manage_access", "accessAdmin.users.canManageAccess"],
  ["can_edit_extraction", "accessAdmin.users.canEditExtraction"],
  ["can_manage_lifecycle", "accessAdmin.users.canManageLifecycle"],
  ["can_share", "accessAdmin.users.canShare"],
] as const;

function UserRow({ user, groups }: { user: AdminUser; groups: Group[] }) {
  const { t } = useTranslation();
  const updateUser      = useUpdateAdminUser();
  const addToGroup      = useAddUserToGroup();
  const removeFromGroup = useRemoveUserFromGroup();
  const [addingGroupId, setAddingGroupId] = useState("");

  const memberGroups   = groups.filter((g) => user.group_ids.includes(g.id));
  const availableGroups = groups.filter((g) => !user.group_ids.includes(g.id));
  const update = (body: UpdateUserBody) => updateUser.mutate({ userId: user.id, body });

  return (
    <div className="py-3 border-b border-gray-100 last:border-0">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="text-sm font-medium text-gray-800 truncate">
            {user.full_name ?? user.email}
          </span>
          <span className="text-xs text-gray-400 ml-2">{user.email}</span>
          {user.role === "admin" ? (
            <span className="inline-block ml-2 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-indigo-100 text-indigo-700">
              {t("roles.admin")}
            </span>
          ) : (
            <div className="flex items-center gap-2 mt-1.5">
              <select
                value={user.role}
                onChange={(e) => update({ role: e.target.value as "reviewer" | "reader" })}
                aria-label={t("accessAdmin.users.role")}
                className="h-7 rounded-md border border-gray-300 px-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <option value="reviewer">{t("roles.reviewer")}</option>
                <option value="reader">{t("roles.reader")}</option>
              </select>
              <label className="flex items-center gap-1 text-xs text-gray-500">
                {t("accessAdmin.users.clearance")}
                <select
                  value={user.clearance}
                  onChange={(e) => update({ clearance: e.target.value as Confidentiality })}
                  className="h-7 rounded-md border border-gray-300 px-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400"
                >
                  {CONFIDENTIALITY_LEVELS.map((level) => (
                    <option key={level} value={level}>{t(`confidentiality.${level}`)}</option>
                  ))}
                </select>
              </label>
            </div>
          )}
        </div>
        {user.role !== "admin" && (
          <div className="flex flex-col items-start gap-1 flex-shrink-0">
            {USER_FLAGS.map(([flag, label]) => {
              // A reader stays read-only: only sharing applies to it.
              const inert = user.role === "reader" && flag !== "can_share";
              return (
                <label
                  key={flag}
                  className={`flex items-center gap-1.5 text-xs ${inert ? "text-gray-300" : "text-gray-600"}`}
                  title={inert ? t("accessAdmin.users.readerFlagHint") : undefined}
                >
                  <input
                    type="checkbox"
                    checked={user[flag]}
                    disabled={inert}
                    onChange={(e) => update({ [flag]: e.target.checked })}
                    className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-400 disabled:opacity-40"
                  />
                  {t(label)}
                </label>
              );
            })}
          </div>
        )}
      </div>
      {updateUser.isError && (
        <p className="text-xs text-red-600 mt-1">{(updateUser.error as Error).message}</p>
      )}

      <div className="flex items-center flex-wrap gap-1.5 mt-2">
        {memberGroups.map((g) => (
          <span
            key={g.id}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-100 text-amber-800"
          >
            {g.name}
            <button
              onClick={() => removeFromGroup.mutate({ userId: user.id, groupId: g.id })}
              className="text-amber-500 hover:text-amber-900"
              aria-label={t("accessAdmin.users.removeFromGroup", { group: g.name })}
            >
              ✕
            </button>
          </span>
        ))}
        {availableGroups.length > 0 && (
          <select
            value={addingGroupId}
            onChange={(e) => {
              const gid = e.target.value;
              if (gid) {
                addToGroup.mutate({ userId: user.id, groupId: gid });
                setAddingGroupId("");
              }
            }}
            className="text-xs rounded-md border border-gray-300 px-1.5 py-0.5 text-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            <option value="">{t("accessAdmin.users.addToGroup")}</option>
            {availableGroups.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        )}
      </div>
    </div>
  );
}

function UsersPanel({ users, groups, isLoading }: { users: AdminUser[]; groups: Group[]; isLoading: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <h2 className="text-sm font-bold text-gray-900 mb-3">{t("accessAdmin.users.heading")}</h2>
      {isLoading ? (
        <p className="text-sm text-gray-400">{t("accessAdmin.users.loading")}</p>
      ) : users.length === 0 ? (
        <p className="text-sm text-gray-400">{t("accessAdmin.users.none")}</p>
      ) : (
        <div>
          {users.map((u) => (
            <UserRow key={u.id} user={u} groups={groups} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Series / document-type grants ────────────────────────────────────────────

function ScopedGrantsPanel({ scope }: { scope: "series" | "doc_type" }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const { data, isLoading } = useScopedGrants(scope);
  const { data: series } = useSeries();
  const { data: types } = useDbTypes();
  const { data: grantees } = useGrantees();
  const createMutation = useCreateScopedGrant();
  const updateMutation = useUpdateAccessGrant();
  const deleteMutation = useDeleteGrant();

  const [target, setTarget]           = useState("");
  const [granteeType, setGranteeType] = useState<"group" | "user">("group");
  const [granteeId, setGranteeId]     = useState("");
  const [permission, setPermission]   = useState<PermissionLevel>("read");

  const targets = scope === "series"
    ? (series ?? []).map((s) => ({ id: s.id, label: s.name }))
    : (types ?? []).map((ty) => ({ id: ty.table_name, label: ty.document_type }));
  const targetLabel = (g: AccessGrant) =>
    scope === "series"
      ? (g.series_name ?? "?")
      : (types?.find((ty) => ty.table_name === g.table_name)?.document_type ?? g.table_name ?? "?");
  const granteeOptions = granteeType === "group" ? (grantees?.groups ?? []) : (grantees?.users ?? []);
  const prefix = scope === "series" ? "accessAdmin.seriesGrants" : "accessAdmin.typeGrants";

  function submit() {
    if (!target || !granteeId) return;
    createMutation.mutate(
      {
        scope,
        ...(scope === "series" ? { series_id: target } : { table_name: target }),
        grantee_type: granteeType,
        grantee_id: granteeId,
        permission,
      },
      { onSuccess: () => setGranteeId("") },
    );
  }

  async function remove(grantId: string) {
    if (!(await confirm({ message: t("accessAdmin.scopedGrants.confirmRemove"), danger: true }))) return;
    deleteMutation.mutate(grantId);
  }

  const select = "h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400";

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5">
      <h2 className="text-sm font-bold text-gray-900 mb-1">{t(`${prefix}.heading`)}</h2>
      <p className="text-xs text-gray-500 mb-3">{t(`${prefix}.description`)}</p>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select value={target} onChange={(e) => setTarget(e.target.value)} className={`${select} flex-1 min-w-[10rem]`}>
          <option value="">{t(`${prefix}.selectTarget`)}</option>
          {targets.map((o) => (
            <option key={o.id} value={o.id}>{o.label}</option>
          ))}
        </select>
        <select
          value={granteeType}
          onChange={(e) => { setGranteeType(e.target.value as "group" | "user"); setGranteeId(""); }}
          className={select}
        >
          <option value="group">{t("access.group")}</option>
          <option value="user">{t("access.person")}</option>
        </select>
        <select value={granteeId} onChange={(e) => setGranteeId(e.target.value)} className={`${select} flex-1 min-w-[10rem]`}>
          <option value="">{granteeType === "group" ? t("access.selectGroup") : t("access.selectPerson")}</option>
          {granteeOptions.map((o) => (
            <option key={o.id} value={o.id}>{"name" in o ? o.name : (o.full_name ?? o.email)}</option>
          ))}
        </select>
        <PermissionSelect value={permission} onChange={setPermission} />
        <button
          onClick={submit}
          disabled={!target || !granteeId || createMutation.isPending}
          className="px-3 py-1.5 rounded-md text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {createMutation.isPending ? t("access.granting") : t("access.grantAccess")}
        </button>
      </div>
      {createMutation.isError && (
        <p className="text-xs text-red-600 mb-3">{(createMutation.error as Error).message}</p>
      )}

      {isLoading ? (
        <p className="text-sm text-gray-400">{t("accessAdmin.scopedGrants.loading")}</p>
      ) : (data?.grants ?? []).length === 0 ? (
        <p className="text-sm text-gray-400">{t(`${prefix}.none`)}</p>
      ) : (
        <div className="divide-y divide-gray-100">
          {(data?.grants ?? []).map((g) => (
            <div key={g.id} className="flex items-center justify-between gap-2 py-2">
              <div className="min-w-0 text-sm">
                <span className="font-medium text-gray-800">{targetLabel(g)}</span>
                <span className="text-gray-400 mx-1.5">→</span>
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold bg-amber-100 text-amber-800 mr-1.5">
                  {g.grantee_type === "group" ? t("access.group") : t("access.person")}
                </span>
                <span className="text-gray-700">{g.grantee_name}</span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <PermissionSelect
                  value={g.permission}
                  onChange={(level) => updateMutation.mutate({ grantId: g.id, permission: level })}
                  disabled={updateMutation.isPending}
                />
                <button
                  onClick={() => remove(g.id)}
                  className="text-xs text-gray-400 hover:text-red-600 transition-colors"
                  aria-label={t("access.removeGrant")}
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AccessAdminPage() {
  const { t } = useTranslation();
  const { data: groups, isLoading: groupsLoading } = useGroups();
  const { data: users, isLoading: usersLoading }   = useAdminUsers();
  const { data: series, isLoading: seriesLoading }  = useSeries();

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
          <h1 className="text-lg font-bold text-gray-900">{t("accessAdmin.header")}</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {t("accessAdmin.subtitle")}
          </p>
        </header>

        <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-3xl">
          <GroupsPanel groups={groups ?? []} isLoading={groupsLoading} />
          <UsersPanel users={users ?? []} groups={groups ?? []} isLoading={usersLoading} />
          <SeriesPanel series={series ?? []} isLoading={seriesLoading} />
          <ScopedGrantsPanel scope="series" />
          <ScopedGrantsPanel scope="doc_type" />
          <IntegrityCheckPanel />
          <ExportArchivePanel />
        </div>
      </div>
    </div>
  );
}
