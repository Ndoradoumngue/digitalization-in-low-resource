interface Props {
  value: number;
}

function colorClass(v: number): string {
  if (v >= 0.8) return "bg-emerald-100 text-emerald-800";
  if (v >= 0.5) return "bg-amber-100 text-amber-800";
  return "bg-red-100 text-red-800";
}

export default function ConfidenceBadge({ value }: Props) {
  const pct = Math.round(value * 100);
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${colorClass(value)}`}>
      {pct}%
    </span>
  );
}
