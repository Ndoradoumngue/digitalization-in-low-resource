import { useState } from "react";

const SIZES = {
  sm: "w-7 h-7 text-[11px]",
  md: "w-9 h-9 text-xs",
  lg: "w-24 h-24 text-2xl",
} as const;

function initials(name: string | null | undefined, email: string | null | undefined): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (email ?? "?").slice(0, 1).toUpperCase();
}

// Someone's profile picture, or their initials when they have none (or it
// fails to load). The URL is versioned by the API, so it can be cached.
export default function Avatar({
  url,
  name,
  email,
  size = "md",
}: {
  url?: string | null;
  name?: string | null;
  email?: string | null;
  size?: keyof typeof SIZES;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const cls = `${SIZES[size]} flex-shrink-0 rounded-full`;
  if (url && failed !== url) {
    return (
      <img src={url} alt="" onError={() => setFailed(url)}
        className={`${cls} object-cover bg-gray-100`} />
    );
  }
  return (
    <span aria-hidden="true"
      className={`${cls} inline-flex items-center justify-center bg-indigo-100 text-indigo-700 font-semibold`}>
      {initials(name, email)}
    </span>
  );
}
