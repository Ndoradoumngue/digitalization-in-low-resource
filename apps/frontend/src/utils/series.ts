import type { TFunction } from "i18next";
import { buildSeriesTree, type Series, type SeriesNode } from "@sdai/types";

/** Display name of a series: the system "to be filed" series is shown in the
 * UI language whatever its stored name; a classification code prefixes it. */
export function seriesLabel(series: Series, t: TFunction, withCode = true): string {
  const name = series.system_key === "to_be_filed" ? t("series.toBeFiled") : series.name;
  return withCode && series.code ? `${series.code} ${name}` : name;
}

/** Depth-first flattening of the tree for indented <select> options. */
export function flattenTree(series: Series[]): { series: Series; depth: number }[] {
  const out: { series: Series; depth: number }[] = [];
  const walk = (nodes: SeriesNode[], depth: number) => {
    for (const node of nodes) {
      out.push({ series: node, depth });
      walk(node.children, depth + 1);
    }
  };
  walk(buildSeriesTree(series), 0);
  return out;
}

/** Documents visible in a node and everything below it. */
export function subtreeCount(node: SeriesNode): number {
  return (node.document_count ?? 0) + node.children.reduce((n, c) => n + subtreeCount(c), 0);
}
