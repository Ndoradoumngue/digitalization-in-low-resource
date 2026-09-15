import { useState } from "react";
import type { AccessGrant } from "@sdai/types";
import { useDocumentAccess, useCreateDocumentAccess, useDeleteDocumentAccess } from "../hooks/useAccess";
import { useGroups, useAdminUsers } from "../hooks/useAdmin";

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
  return (
    <div className="flex items-center justify-between gap-2 py-2 border-b border-gray-100 last:border-0">
      <div className="flex-1 min-w-0">
        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold bg-amber-100 text-amber-800 mr-2">
          {grant.grantee_type === "group" ? "Group" : "Person"}
        </span>
        <span className="text-sm text-gray-800 truncate">{grant.grantee_name}</span>
      </div>
      {canManage && (
        <button
          onClick={onRemove}
          disabled={removing}
          className="flex-shrink-0 text-xs text-gray-400 hover:text-red-600 disabled:opacity-40 transition-colors"
          title="Remove access grant"
          aria-label="Remove access grant"
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
  const [granteeType, setGranteeType] = useState<"group" | "user">("group");
  const [granteeId, setGranteeId]     = useState("");
  const { data: groups }   = useGroups();
  const { data: users }    = useAdminUsers();
  const createMutation = useCreateDocumentAccess(tableName, id);

  function submit() {
    if (!granteeId) return;
    createMutation.mutate(
      { grantee_type: granteeType, grantee_id: granteeId },
      { onSuccess: onDone },
    );
  }

  const options = granteeType === "group" ? (groups ?? []) : (users ?? []);

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
          <option value="group">Group</option>
          <option value="user">Person</option>
        </select>
        <select
          value={granteeId}
          onChange={(e) => setGranteeId(e.target.value)}
          className="h-8 flex-1 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <option value="">Select {granteeType === "group" ? "a group" : "a person"}…</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {"name" in o ? o.name : (o.full_name ?? o.email)}
            </option>
          ))}
        </select>
      </div>

      {createMutation.isError && (
        <p className="text-xs text-red-600">{(createMutation.error as Error).message}</p>
      )}

      <div className="flex justify-end gap-2">
        <button
          onClick={onDone}
          className="px-2.5 py-1.5 rounded-md text-xs border border-gray-300 hover:bg-gray-100 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={submit}
          disabled={!granteeId || createMutation.isPending}
          className="px-2.5 py-1.5 rounded-md text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {createMutation.isPending ? "Granting…" : "Grant access"}
        </button>
      </div>
    </div>
  );
}

export default function AccessSection({ tableName, id }: { tableName: string; id: string }) {
  const { data, isLoading } = useDocumentAccess(tableName, id);
  const deleteMutation = useDeleteDocumentAccess(tableName, id);
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
          Access restrictions
        </h3>
        {canManage && (
          <button
            onClick={() => setShowPicker((s) => !s)}
            className="text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
          >
            {showPicker ? "Cancel" : "+ Restrict to…"}
          </button>
        )}
      </div>

      {showPicker && (
        <AccessGrantPicker tableName={tableName} id={id} onDone={() => setShowPicker(false)} />
      )}

      {isLoading ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : grants.length === 0 ? (
        <p className="text-sm text-gray-400">
          Visible to everyone in your organization. No restrictions applied.
        </p>
      ) : (
        <div>
          <p className="text-xs text-gray-400 mb-1">
            Restricted — visible only to admins and:
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
