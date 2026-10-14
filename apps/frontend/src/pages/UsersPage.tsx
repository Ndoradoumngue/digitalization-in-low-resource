import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { CONFIDENTIALITY_LEVELS, USER_FLAGS, type AdminUser, type Confidentiality, type Group, type User, type UserFlag } from "@sdai/types";
import type { UpdateUserBody } from "@sdai/api-client";
import NavSidebar from "../components/NavSidebar";
import { useAuth } from "../context/AuthContext";
import { useConfirm } from "../context/ConfirmContext";
import {
  useAddUserToGroup,
  useAdminUsers,
  useCreateAdminUser,
  useDeleteAdminUser,
  useGroups,
  useRemoveUserFromGroup,
  useResetUserPassword,
  useSetUserActive,
  useUpdateAdminUser,
} from "../hooks/useAdmin";
import { formatDateTime } from "../utils/dates";

// "Utilisateurs" (GED Phase 9): admins and reviewers with the user
// management permission add, update, (de)activate accounts and reset
// passwords; only admins delete (soft - users_router.py). The controls
// mirror the backend rules, which stay the authority.

const ROLES = ["reviewer", "reader", "admin"] as const;
const level = (c: string) => CONFIDENTIALITY_LEVELS.indexOf(c as Confidentiality);

/** 12 characters without look-alikes (0/O, 1/l/I), to read out or copy. */
function generatePassword(): string {
  const alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

function canTouch(me: User, target: AdminUser): boolean {
  if (me.role === "admin") return true;
  return target.role !== "admin" && level(target.clearance) <= level(me.clearance);
}

function holds(me: User, flag: UserFlag): boolean {
  return me.role === "admin" || !!me[flag as keyof User];
}

const inputClass =
  "h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400";

// ── Shared editor fields (create form and row editor) ─────────────────────────

function RightsFields({
  me,
  value,
  onChange,
  disabled,
}: {
  me: User;
  value: Partial<Record<UserFlag, boolean>> & { role?: string; clearance?: string };
  onChange: (changes: UpdateUserBody) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const roles = ROLES.filter((r) => r !== "admin" || me.role === "admin" || value.role === "admin");
  const levels = CONFIDENTIALITY_LEVELS.filter(
    (c) => me.role === "admin" || level(c) <= level(me.clearance) || c === value.clearance,
  );
  return (
    <div className="flex flex-wrap items-start gap-x-6 gap-y-2">
      <label className="text-xs text-gray-600">
        <span className="block font-medium">{t("accessAdmin.users.role")}</span>
        <select disabled={disabled} value={value.role} className={`${inputClass} mt-0.5`}
          onChange={(e) => onChange({ role: e.target.value as UpdateUserBody["role"] })}>
          {roles.map((r) => <option key={r} value={r}>{t(`roles.${r}`)}</option>)}
        </select>
      </label>
      <label className="text-xs text-gray-600">
        <span className="block font-medium">{t("accessAdmin.users.clearance")}</span>
        <select disabled={disabled} value={value.clearance} className={`${inputClass} mt-0.5`}
          onChange={(e) => onChange({ clearance: e.target.value as Confidentiality })}>
          {levels.map((c) => <option key={c} value={c}>{t(`confidentiality.${c}`)}</option>)}
        </select>
      </label>
      {value.role !== "admin" && (
        <fieldset className="space-y-1">
          <legend className="text-xs font-medium text-gray-600">{t("users.permissions")}</legend>
          {USER_FLAGS.map((flag) => {
            // A reader stays read-only: only sharing applies to it.
            const inert = value.role === "reader" && flag !== "can_share";
            const allowed = holds(me, flag);
            return (
              <label key={flag}
                className={`flex items-center gap-1.5 text-xs ${inert || !allowed ? "text-gray-300" : "text-gray-600"}`}
                title={!allowed ? t("users.notYourPermission") : inert ? t("accessAdmin.users.readerFlagHint") : undefined}>
                <input type="checkbox" checked={!!value[flag]} disabled={disabled || inert || !allowed}
                  onChange={(e) => onChange({ [flag]: e.target.checked })}
                  className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-400 disabled:opacity-40" />
                {t(`users.flags.${flag}`)}
              </label>
            );
          })}
        </fieldset>
      )}
    </div>
  );
}

// ── Create ────────────────────────────────────────────────────────────────────

function CreateUserForm({ me, onDone }: { me: User; onDone: (created: { email: string; password: string }) => void }) {
  const { t } = useTranslation();
  const create = useCreateAdminUser();
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState(generatePassword);
  const [rights, setRights] = useState<UpdateUserBody>({ role: "reviewer", clearance: "internal" });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { ...rights, email, full_name: fullName || undefined, temporary_password: password },
      { onSuccess: () => onDone({ email, password }) },
    );
  }

  return (
    <form onSubmit={submit} className="bg-white rounded-lg border border-indigo-200 p-5 space-y-4">
      <h2 className="text-sm font-bold text-gray-900">{t("users.new")}</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-gray-600">
          <span className="block font-medium">{t("users.email")}</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className={`${inputClass} w-full mt-0.5`} />
        </label>
        <label className="text-xs text-gray-600">
          <span className="block font-medium">{t("users.fullName")}</span>
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} maxLength={200}
            className={`${inputClass} w-full mt-0.5`} />
        </label>
      </div>
      <RightsFields me={me} value={rights} onChange={(c) => setRights({ ...rights, ...c })} />
      <div className="text-xs text-gray-600">
        <label htmlFor="new-user-password" className="block font-medium">{t("users.temporaryPassword")}</label>
        <span className="flex gap-2 mt-0.5">
          <input id="new-user-password" required minLength={8} value={password} aria-describedby="new-user-password-hint"
            onChange={(e) => setPassword(e.target.value)} className={`${inputClass} font-mono flex-1`} />
          <button type="button" onClick={() => setPassword(generatePassword())}
            className="h-8 px-3 rounded-md border border-gray-300 text-xs hover:bg-gray-50">{t("users.generate")}</button>
        </span>
        <p id="new-user-password-hint" className="text-gray-500 mt-0.5">{t("users.temporaryHint")}</p>
      </div>
      {create.isError && <p role="alert" className="text-sm text-red-600">{(create.error as Error).message}</p>}
      <button type="submit" disabled={create.isPending}
        className="h-9 px-4 rounded-md bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50">
        {create.isPending ? t("users.creating") : t("users.create")}
      </button>
    </form>
  );
}

// ── One account ───────────────────────────────────────────────────────────────

function UserRow({ me, user, groups }: { me: User; user: AdminUser; groups: Group[] | null }) {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  const update = useUpdateAdminUser();
  const setActive = useSetUserActive();
  const reset = useResetUserPassword();
  const remove = useDeleteAdminUser();
  const addToGroup = useAddUserToGroup();
  const removeFromGroup = useRemoveUserFromGroup();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(user.full_name ?? "");
  const [newPassword, setNewPassword] = useState<string | null>(null);

  const self = user.id === me.id;
  const touchable = canTouch(me, user);
  const error = [update, setActive, reset, remove].find((m) => m.isError)?.error as Error | undefined;
  const patch = (body: UpdateUserBody) => update.mutate({ userId: user.id, body });

  async function deactivate() {
    if (await confirm({ message: t("users.deactivateConfirm", { name: user.full_name ?? user.email }), danger: true })) {
      setActive.mutate({ userId: user.id, active: false });
    }
  }

  async function resetPassword() {
    const password = generatePassword();
    if (await confirm({ message: t("users.resetConfirm", { name: user.full_name ?? user.email }) })) {
      reset.mutate({ userId: user.id, password }, { onSuccess: () => setNewPassword(password) });
    }
  }

  async function del() {
    if (await confirm({ title: t("users.deleteTitle"), message: t("users.deleteConfirm", { name: user.full_name ?? user.email }), danger: true, confirmLabel: t("users.delete") })) {
      remove.mutate(user.id);
    }
  }

  return (
    <li className={`py-3 ${user.is_active ? "" : "opacity-70"}`}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-gray-800 truncate">
            {user.full_name ?? user.email}
            {self && <span className="ml-1 text-xs text-gray-400">({t("users.you")})</span>}
          </p>
          <p className="text-xs text-gray-500 truncate">{user.email}</p>
          <div className="flex flex-wrap gap-1 mt-1">
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${user.role === "admin" ? "bg-indigo-100 text-indigo-700" : "bg-gray-100 text-gray-600"}`}>
              {t(`roles.${user.role}`)}
            </span>
            <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-gray-600">
              {t(`confidentiality.${user.clearance}`)}
            </span>
            {!user.is_active && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-red-100 text-red-700">{t("users.inactive")}</span>
            )}
            {user.must_change_password && user.is_active && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-100 text-amber-800">{t("users.mustChange")}</span>
            )}
          </div>
          <p className="text-[11px] text-gray-400 mt-1">
            {user.last_login_at
              ? t("users.lastLogin", { date: formatDateTime(user.last_login_at, i18n.language) })
              : t("users.neverLoggedIn")}
          </p>
        </div>
        {touchable && (
          <button onClick={() => setOpen(!open)} className="text-xs text-indigo-600 hover:underline flex-shrink-0">
            {open ? t("users.close") : t("users.manage")}
          </button>
        )}
      </div>

      {newPassword && (
        <p className="mt-2 rounded-md bg-emerald-50 border border-emerald-200 px-3 py-2 text-xs text-emerald-800">
          {t("users.newTemporary")} <code className="font-mono font-semibold select-all">{newPassword}</code>
        </p>
      )}

      {open && touchable && (
        <div className="mt-3 space-y-3 rounded-md bg-gray-50 p-3">
          <label className="block text-xs text-gray-600">
            <span className="block font-medium">{t("users.fullName")}</span>
            <span className="flex gap-2 mt-0.5">
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} className={`${inputClass} flex-1`} />
              <button disabled={name === (user.full_name ?? "")} onClick={() => patch({ full_name: name })}
                className="h-8 px-3 rounded-md border border-gray-300 text-xs hover:bg-white disabled:opacity-40">{t("users.save")}</button>
            </span>
          </label>
          {self ? (
            <p className="text-xs text-gray-500">{t("users.ownRightsNote")}</p>
          ) : (
            <RightsFields me={me} value={user} onChange={patch} disabled={update.isPending} />
          )}

          {groups && !self && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs font-medium text-gray-600">{t("accessAdmin.groups.heading")}</span>
              {groups.filter((g) => user.group_ids.includes(g.id)).map((g) => (
                <span key={g.id} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-100 text-amber-800">
                  {g.name}
                  <button onClick={() => removeFromGroup.mutate({ userId: user.id, groupId: g.id })}
                    aria-label={t("accessAdmin.users.removeFromGroup", { group: g.name })} className="text-amber-500 hover:text-amber-900">✕</button>
                </span>
              ))}
              {groups.some((g) => !user.group_ids.includes(g.id)) && (
                <select value="" onChange={(e) => e.target.value && addToGroup.mutate({ userId: user.id, groupId: e.target.value })}
                  className="text-xs rounded-md border border-gray-300 px-1.5 py-0.5 text-gray-500">
                  <option value="">{t("accessAdmin.users.addToGroup")}</option>
                  {groups.filter((g) => !user.group_ids.includes(g.id)).map((g) => (
                    <option key={g.id} value={g.id}>{g.name}</option>
                  ))}
                </select>
              )}
            </div>
          )}

          {!self && (
            <div className="flex flex-wrap gap-2 pt-1">
              {user.is_active ? (
                <button onClick={deactivate} className="h-8 px-3 rounded-md border border-red-200 text-xs text-red-700 hover:bg-red-50">{t("users.deactivate")}</button>
              ) : (
                <button onClick={() => setActive.mutate({ userId: user.id, active: true })}
                  className="h-8 px-3 rounded-md border border-emerald-300 text-xs text-emerald-700 hover:bg-emerald-50">{t("users.activate")}</button>
              )}
              <button onClick={resetPassword} className="h-8 px-3 rounded-md border border-gray-300 text-xs hover:bg-white">{t("users.resetPassword")}</button>
              {me.role === "admin" && (
                <button onClick={del} className="h-8 px-3 rounded-md text-xs text-red-600 hover:underline ml-auto">{t("users.delete")}</button>
              )}
            </div>
          )}
          {error && <p role="alert" className="text-xs text-red-600">{error.message}</p>}
        </div>
      )}
    </li>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function UsersPage() {
  const { t } = useTranslation();
  const { user: me } = useAuth();
  const { data: users, isLoading, isError } = useAdminUsers();
  // Group membership grants document access: admins only.
  const { data: groups } = useGroups(me?.role === "admin");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null);
  const [query, setQuery] = useState("");
  const [showInactive, setShowInactive] = useState(false);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (users ?? []).filter(
      (u) =>
        (showInactive || u.is_active) &&
        (!q || u.email.toLowerCase().includes(q) || (u.full_name ?? "").toLowerCase().includes(q)),
    );
  }, [users, query, showInactive]);
  const inactiveCount = (users ?? []).filter((u) => !u.is_active).length;

  if (!me) return null;
  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0 flex items-center gap-4">
          <div className="flex-1">
            <h1 className="text-lg font-bold text-gray-900">{t("users.title")}</h1>
            <p className="text-xs text-gray-500 mt-0.5">{t("users.subtitle")}</p>
          </div>
          <button onClick={() => { setCreating(!creating); setCreated(null); }}
            className="h-9 px-4 rounded-md bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700">
            {creating ? t("users.close") : t("users.new")}
          </button>
        </header>
        <div className="flex-1 overflow-y-auto p-6 space-y-4 max-w-3xl">
          {created && (
            <div role="status" className="rounded-md bg-emerald-50 border border-emerald-200 px-4 py-3 text-sm text-emerald-900">
              {t("users.createdNote", { email: created.email })}{" "}
              <code className="font-mono font-semibold select-all">{created.password}</code>
            </div>
          )}
          {creating && (
            <CreateUserForm me={me} onDone={(c) => { setCreated(c); setCreating(false); }} />
          )}
          <div className="bg-white rounded-lg border border-gray-200 p-5">
            <div className="flex flex-wrap items-center gap-3 mb-2">
              <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder={t("users.search")} className={`${inputClass} flex-1 min-w-48`} />
              <label className="flex items-center gap-1.5 text-xs text-gray-600">
                <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)}
                  className="rounded border-gray-300 text-indigo-600" />
                {t("users.showInactive", { count: inactiveCount })}
              </label>
            </div>
            {isLoading ? (
              <p className="text-sm text-gray-400">{t("accessAdmin.users.loading")}</p>
            ) : isError ? (
              <p className="text-sm text-red-600">{t("users.loadError")}</p>
            ) : shown.length === 0 ? (
              <p className="text-sm text-gray-400">{t("accessAdmin.users.none")}</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {shown.map((u) => <UserRow key={u.id} me={me} user={u} groups={groups ?? null} />)}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
