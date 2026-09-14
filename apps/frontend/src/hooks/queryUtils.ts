// api-client's fetchJson throws `Error("HTTP <status>: <url>")` on a
// non-OK response. A 4xx (unauthorized, forbidden, not found, ...) won't
// resolve itself by retrying, so polling/retry loops should stop instead
// of hammering the server forever once a session expires or a resource
// disappears.
export function isClientError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const match = error.message.match(/^HTTP (\d+)/);
  return !!match && Number(match[1]) >= 400 && Number(match[1]) < 500;
}

// Narrower than isClientError: only auth failures (expired/invalid
// session), which truly won't resolve on their own. Some 404s are
// legitimately transient — e.g. GET /api/ingest/pages/{id} 404s until
// Stage 1 finishes creating page rows, which can take a while for a
// large PDF — so a poll shouldn't give up permanently just because it
// checked before that finished.
export function isAuthError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return /^HTTP (401|403)/.test(error.message);
}
