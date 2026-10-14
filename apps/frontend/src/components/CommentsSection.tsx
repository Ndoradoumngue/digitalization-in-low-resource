import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Comment, MentionableUser } from "@sdai/types";
import { useConfirm } from "../context/ConfirmContext";
import { useCommentAction, useComments, useMentionable } from "../hooks/useCollaboration";
import { formatDateTime } from "../utils/lifecycle";

// "Commentaires" tab (GED Phase 7): threaded comments (one reply level
// shown, replies to replies attach to the same thread), @mentions picked
// from the people who can read the document - they get a notification.
export default function CommentsSection({
  tableName,
  id,
  canPost,
}: {
  tableName: string;
  id: string;
  canPost: boolean;
}) {
  const { t } = useTranslation();
  const { data: comments, isLoading } = useComments(tableName, id);
  const [replyTo, setReplyTo] = useState<Comment | null>(null);

  // Threads: each top-level comment followed by every reply below it.
  const threads = useMemo(() => {
    const list = comments ?? [];
    const byId = new Map(list.map((c) => [c.id, c]));
    const root = (c: Comment): string => {
      let cur = c;
      const seen = new Set<string>();
      while (cur.parent_id && byId.has(cur.parent_id) && !seen.has(cur.id)) {
        seen.add(cur.id);
        cur = byId.get(cur.parent_id)!;
      }
      return cur.id;
    };
    const groups = new Map<string, Comment[]>();
    for (const c of list) {
      const r = root(c);
      if (!groups.has(r)) groups.set(r, []);
      if (r !== c.id) groups.get(r)!.push(c);
    }
    return list.filter((c) => root(c) === c.id).map((c) => ({ head: c, replies: groups.get(c.id) ?? [] }));
  }, [comments]);

  return (
    <div className="space-y-4">
      {isLoading && <p className="text-sm text-gray-400">{t("common.loading")}</p>}
      {!isLoading && threads.length === 0 && <p className="text-sm text-gray-400">{t("comments.empty")}</p>}
      {threads.map(({ head, replies }) => (
        <div key={head.id} className="space-y-2">
          <CommentItem comment={head} tableName={tableName} id={id} onReply={canPost ? setReplyTo : undefined} />
          {replies.length > 0 && (
            <div className="ml-6 pl-3 border-l-2 border-gray-100 space-y-2">
              {replies.map((r) => (
                <CommentItem key={r.id} comment={r} tableName={tableName} id={id} onReply={canPost ? setReplyTo : undefined} />
              ))}
            </div>
          )}
        </div>
      ))}
      {canPost ? (
        <Composer tableName={tableName} id={id} replyTo={replyTo} onDone={() => setReplyTo(null)} />
      ) : (
        <p className="text-xs text-gray-400">{t("comments.readOnly")}</p>
      )}
    </div>
  );
}

function renderBody(body: string, mentions: { id: string; name: string }[]) {
  if (!mentions.length) return body;
  const names = mentions.map((m) => m.name).sort((a, b) => b.length - a.length);
  const escaped = names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = body.split(new RegExp(`(@(?:${escaped.join("|")}))`, "g"));
  return parts.map((part, i) =>
    part.startsWith("@") && names.includes(part.slice(1)) ? (
      <span key={i} className="font-medium text-indigo-700 bg-indigo-50 rounded px-0.5">{part}</span>
    ) : (
      part
    ),
  );
}

function CommentItem({
  comment,
  tableName,
  id,
  onReply,
}: {
  comment: Comment;
  tableName: string;
  id: string;
  onReply?: (c: Comment) => void;
}) {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  const action = useCommentAction(tableName, id);
  const [editing, setEditing] = useState(false);

  async function remove() {
    const ok = await confirm({
      title: t("comments.delete"),
      message: t("comments.deleteConfirm"),
      confirmLabel: t("comments.delete"),
    });
    if (ok) action.mutate({ kind: "delete", commentId: comment.id });
  }

  if (editing) {
    return (
      <Composer
        tableName={tableName}
        id={id}
        editing={comment}
        onDone={() => setEditing(false)}
      />
    );
  }
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
      <div className="flex items-center gap-2 text-xs text-gray-500">
        <span className="font-semibold text-gray-700">{comment.author_name ?? "?"}</span>
        <span>{formatDateTime(comment.created_at, i18n.language)}</span>
        {comment.version_no != null && <span className="font-mono text-[11px] text-gray-400">v{comment.version_no}</span>}
        {comment.edited_at && !comment.deleted && <span className="italic">{t("comments.edited")}</span>}
        <span className="flex-1" />
        {onReply && !comment.deleted && (
          <button onClick={() => onReply(comment)} className="text-indigo-600 hover:text-indigo-800">{t("comments.reply")}</button>
        )}
        {comment.can_edit && (
          <button onClick={() => setEditing(true)} className="text-gray-500 hover:text-gray-800">{t("comments.edit")}</button>
        )}
        {comment.can_delete && (
          <button onClick={remove} disabled={action.isPending} className="text-red-500 hover:text-red-700">{t("comments.delete")}</button>
        )}
      </div>
      <p className={`mt-1 text-sm whitespace-pre-wrap break-words ${comment.deleted ? "italic text-gray-400" : "text-gray-800"}`}>
        {comment.deleted ? t("comments.deleted") : renderBody(comment.body ?? "", comment.mentions)}
      </p>
    </div>
  );
}

// The @word being typed just before the caret, if any.
function mentionQuery(text: string, caret: number): { start: number; q: string } | null {
  const before = text.slice(0, caret);
  const m = /(^|\s)@([^\s@]{0,30})$/.exec(before);
  return m ? { start: caret - m[2].length - 1, q: m[2] } : null;
}

function Composer({
  tableName,
  id,
  replyTo,
  editing,
  onDone,
}: {
  tableName: string;
  id: string;
  replyTo?: Comment | null;
  editing?: Comment;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const action = useCommentAction(tableName, id);
  const [body, setBody] = useState(editing?.body ?? "");
  const [picked, setPicked] = useState<{ id: string; name: string }[]>(editing?.mentions ?? []);
  const [mention, setMention] = useState<{ start: number; q: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const { data: candidates } = useMentionable(tableName, id, mention?.q ?? "", mention !== null);

  function onChange(value: string, caret: number) {
    setBody(value);
    setMention(mentionQuery(value, caret));
  }

  function pick(u: MentionableUser) {
    if (!mention) return;
    const caret = mention.start + 1 + mention.q.length;
    const next = `${body.slice(0, mention.start)}@${u.name} ${body.slice(caret)}`;
    setBody(next);
    setPicked((p) => (p.some((x) => x.id === u.id) ? p : [...p, { id: u.id, name: u.name }]));
    setMention(null);
    requestAnimationFrame(() => ref.current?.focus());
  }

  function submit() {
    const text = body.trim();
    if (!text) return;
    // Only the people whose @Name is still in the text.
    const mentions = picked.filter((m) => text.includes(`@${m.name}`)).map((m) => m.id);
    setError(null);
    const done = {
      onSuccess: () => {
        setBody("");
        setPicked([]);
        onDone();
      },
      onError: (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    };
    if (editing) action.mutate({ kind: "edit", commentId: editing.id, body: text, mentions }, done);
    else action.mutate({ kind: "add", body: text, parentId: replyTo?.id ?? null, mentions }, done);
  }

  return (
    <div className="relative rounded-lg border border-gray-200 bg-gray-50 p-2 space-y-2">
      {replyTo && (
        <p className="text-xs text-gray-500">
          {t("comments.replyingTo", { name: replyTo.author_name ?? "?" })}{" "}
          <button onClick={onDone} className="text-indigo-600 hover:underline">{t("common.cancel")}</button>
        </p>
      )}
      <textarea
        ref={ref}
        value={body}
        onChange={(e) => onChange(e.target.value, e.target.selectionStart)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setMention(null);
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
        }}
        rows={3}
        maxLength={5000}
        placeholder={t("comments.placeholder")}
        className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        aria-label={t("comments.placeholder")}
      />
      {mention && (candidates ?? []).length > 0 && (
        <ul className="absolute left-2 z-20 w-64 rounded-md border border-gray-200 bg-white shadow-lg py-1" role="listbox">
          {(candidates ?? []).map((u) => (
            <li key={u.id}>
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(u);
                }}
                className="w-full text-left px-3 py-1.5 text-sm hover:bg-indigo-50"
              >
                <span className="font-medium">{u.name}</span>
                <span className="ml-1 text-xs text-gray-400">{u.email}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-gray-400">{t("comments.mentionHint")}</span>
        <div className="flex gap-2">
          {editing && (
            <button onClick={onDone} className="px-3 py-1 rounded-md text-xs text-gray-600 hover:bg-gray-100">
              {t("common.cancel")}
            </button>
          )}
          <button
            onClick={submit}
            disabled={!body.trim() || action.isPending}
            className="px-3 py-1 rounded-md text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
          >
            {editing ? t("common.save") : replyTo ? t("comments.reply") : t("comments.post")}
          </button>
        </div>
      </div>
    </div>
  );
}
