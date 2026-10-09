import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CONFIDENTIALITY_LEVELS,
  PERMISSION_LEVELS,
  type AccessGrant,
  type Confidentiality,
  type PermissionLevel,
} from "@sdai/types";
import {
  useDocumentAccess,
  useCreateDocumentAccess,
  useDeleteGrant,
  useGrantees,
  useSetConfidentiality,
  useUpdateAccessGrant,
} from "../hooks/useAccess";
import { useAuth } from "../context/AuthContext";
import ConfidentialityBadge from "./ConfidentialityBadge";

export function PermissionSelect({
  value,
  onChange,
  disabled,
}: {
  value: PermissionLevel;
  onChange: (level: PermissionLevel) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as PermissionLevel)}
      disabled={disabled}
      className="h-7 rounded-md border border-gray-300 px-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400 disabled:opacity-50"
    >
      {PERMISSION_LEVELS.map((level) => (
        <option key={level} value={level}>{t(`access.permission.${level}`)}</option>
      ))}
    </select>
  );
}

function GrantRow({
  grant,
  onRemove,
  removing,
  canManage,
}: {
  grant: AccessGrant;
  onRemove: () => void;
  removing: boolean;
  canManage: boolean;
}) {
  const { t } = useTranslation();
  const updateMutation = useUpdateAccessGrant();
  // Series/type grants are managed on the access admin page, not per document.
  const own = grant.scope === "document";
  return (
    <div className="flex items-center justify-between gap-2 py-2 border-b border-gray-100 last:border-0">
      <div className="flex-1 min-w-0">
        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold bg-amber-100 text-amber-800 mr-2">
          {grant.grantee_type === "group" ? t("access.group") : t("access.person")}
        </span>
        <span className="text-sm text-gray-800 truncate">{grant.grantee_name}</span>
        {!own && (
          <span className="ml-2 text-[11px] text-gray-400">
            {grant.scope === "series"
              ? t("access.viaSeries", { name: grant.series_name ?? "?" })
              : t("access.viaType")}
          </span>
        )}
      </div>
      {canManage && own ? (
        <PermissionSelect
          value={grant.permission}
          onChange={(permission) => updateMutation.mutate({ grantId: grant.id, permission })}
          disabled={updateMutation.isPending}
        />
      ) : (
        <span className="flex-shrink-0 text-[11px] font-medium text-gray-500">
          {t(`access.permission.${grant.permission}`)}
        </span>
      )}
      {canManage && own && (
        <button
          onClick={onRemove}
          disabled={removing}
          className="flex-shrink-0 text-xs text-gray-400 hover:text-red-600 disabled:opacity-40 transition-colors"
          title={t("access.removeGrant")}
          aria-label={t("access.removeGrant")}
        >
          ✕
        </button>
      )}
    </div>
  );
}

function AccessGrantPicker({
  tableName,
  id,
  onDone,
}: {
  tableName: string;
  id: string;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [granteeType, setGranteeType] = useState<"group" | "user">("group");
  const [granteeId, setGranteeId]     = useState("");
  const [permission, setPermission]   = useState<PermissionLevel>("download");
  const { data: grantees } = useGrantees();
  const createMutation = useCreateDocumentAccess(tableName, id);

  function submit() {
    if (!granteeId) return;
    createMutation.mutate(
      { grantee_type: granteeType, grantee_id: granteeId, permission },
      { onSuccess: onDone },
    );
  }

  const options = granteeType === "group" ? (grantees?.groups ?? []) : (grantees?.users ?? []);

  return (
    <div className="mb-3 p-3 rounded-md border border-gray-200 bg-gray-50 space-y-2">
      <div className="flex items-center gap-2">
        <select
          value={granteeType}
          onChange={(e) => {
            setGranteeType(e.target.value as "group" | "user");
            setGranteeId("");
          }}
          className="h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <option value="group">{t("access.group")}</option>
          <option value="user">{t("access.person")}</option>
        </select>
        <select
          value={granteeId}
          onChange={(e) => setGranteeId(e.target.value)}
          className="h-8 flex-1 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <option value="">{granteeType === "group" ? t("access.selectGroup") : t("access.selectPerson")}</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {"name" in o ? o.name : (o.full_name ?? o.email)}
            </option>
          ))}
        </select>
        <PermissionSelect value={permission} onChange={setPermission} />
      </div>

      {createMutation.isError && (
        <p className="text-xs text-red-600">{(createMutation.error as Error).message}</p>
      )}

      <div className="flex justify-end gap-2">
        <button
          onClick={onDone}
          className="px-2.5 py-1.5 rounded-md text-xs border border-gray-300 hover:bg-gray-100 transition-colors"
        >
          {t("access.cancel")}
        </button>
        <button
          onClick={submit}
          disabled={!granteeId || createMutation.isPending}
          className="px-2.5 py-1.5 rounded-md text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {createMutation.isPending ? t("access.granting") : t("access.grantAccess")}
        </button>
      </div>
    </div>
  );
}

function ConfidentialityControl({
  tableName,
  id,
  value,
  canManage,
}: {
  tableName: string;
  id: string;
  value: Confidentiality;
  canManage: boolean;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const mutation = useSetConfidentiality(tableName, id);
  // A non-admin can't classify above their own clearance (backend rule).
  const maxIndex = user?.role === "admin"
    ? CONFIDENTIALITY_LEVELS.length - 1
    : CONFIDENTIALITY_LEVELS.indexOf(user?.clearance ?? "internal");

  return (
    <div className="flex items-center justify-between gap-2 mb-3">
      <span className="text-xs text-gray-500">{t("confidentiality.label")}</span>
      {canManage ? (
        <select
          value={value}
          onChange={(e) => mutation.mutate(e.target.value as Confidentiality)}
          disabled={mutation.isPending}
          className="h-7 rounded-md border border-gray-300 px-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          {CONFIDENTIALITY_LEVELS.map((level, i) => (
            <option key={level} value={level} disabled={i > maxIndex}>
              {t(`confidentiality.${level}`)}
            </option>
          ))}
        </select>
      ) : (
        <ConfidentialityBadge level={value} />
      )}
      {mutation.isError && (
        <p className="text-xs text-red-600">{(mutation.error as Error).message}</p>
      )}
    </div>
  );
}

export default function AccessSection({
  tableName,
  id,
  confidentiality,
}: {
  tableName: string;
  id: string;
  confidentiality?: Confidentiality;
}) {
  const { t } = useTranslation();
  const { data, isLoading } = useDocumentAccess(tableName, id);
  const deleteMutation = useDeleteGrant();
  const [showPicker, setShowPicker] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const grants    = data?.grants ?? [];
  const canManage = data?.can_manage ?? false;

  function remove(grantId: string) {
    setRemovingId(grantId);
    deleteMutation.mutate(grantId, { onSettled: () => setRemovingId(null) });
  }

  return (
    <div className="pt-4 mt-4 border-t border-gray-200">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
          {t("access.heading")}
        </h3>
        {canManage && (
          <button
            onClick={() => setShowPicker((s) => !s)}
            className="text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
          >
            {showPicker ? t("access.cancel") : t("access.restrictTo")}
          </button>
        )}
      </div>

      {confidentiality && (
        <ConfidentialityControl tableName={tableName} id={id} value={confidentiality} canManage={canManage} />
      )}

      {data?.my_permission && (
        <p className="text-xs text-gray-500 mb-2">
          {t("access.yourAccess", { level: t(`access.permission.${data.my_permission}`) })}
        </p>
      )}

      {showPicker && (
        <AccessGrantPicker tableName={tableName} id={id} onDone={() => setShowPicker(false)} />
      )}

      {isLoading ? (
        <p className="text-sm text-gray-400">{t("access.loading")}</p>
      ) : grants.length === 0 ? (
        <p className="text-sm text-gray-400">
          {t("access.openToAll")}
        </p>
      ) : (
        <div>
          <p className="text-xs text-gray-400 mb-1">
            {t("access.restrictedNote")}
          </p>
          {grants.map((g) => (
            <GrantRow
              key={g.id}
              grant={g}
              onRemove={() => remove(g.id)}
              removing={removingId === g.id}
              canManage={canManage}
            />
          ))}
        </div>
      )}
    </div>
  );
}
