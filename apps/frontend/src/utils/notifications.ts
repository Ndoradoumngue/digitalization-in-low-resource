import type { TFunction } from "i18next";
import { NOTIFICATION_KINDS, type Notification } from "@sdai/types";

// Display text of a notification (GED Phase 7) - built here from its kind
// and payload, in the reader's UI language (the backend stores no text).

export function notificationText(n: Notification, t: TFunction): string {
  const p = n.payload;
  const vars = {
    by: typeof p.by === "string" ? p.by : "?",
    record: typeof p.record_id === "string" && p.record_id ? p.record_id : t("notifications.aDocument"),
    count: typeof p.count === "number" ? p.count : 1,
    node: typeof p.node_id === "string" ? p.node_id : "",
  };
  if (p.unavailable) return t("notifications.unavailable", { kind: t(`notifications.kinds.${n.kind}`, { defaultValue: n.kind }) });
  if ((NOTIFICATION_KINDS as readonly string[]).includes(n.kind)) return t(`notifications.text.${n.kind}`, vars);
  return t("notifications.text.unknown");
}

// Document notifications open the tab they are about.
const KIND_TAB: Record<string, string> = {
  mention: "comments",
  comment_reply: "comments",
  signature_requested: "signatures",
  signature_completed: "signatures",
  signature_declined: "signatures",
};

// Only a path inside the app is ever followed - never another site
// ("//host", "/\\host" or a full URL), whatever the stored link says.
const IN_APP_PATH = /^\/(?![\/\\])[^\s]*$/;

export function notificationLink(n: Notification): string {
  if (!IN_APP_PATH.test(n.link)) return "/notifications";
  const tab = KIND_TAB[n.kind];
  return tab && n.document_id && n.link.startsWith("/documents/") ? `${n.link}?tab=${tab}` : n.link;
}
