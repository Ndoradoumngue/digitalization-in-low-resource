import { useCallback, useEffect, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  MiniMap,
  Background,
  BackgroundVariant,
  MarkerType,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import Dagre from "@dagrejs/dagre";
import { toPng } from "html-to-image";
import { Link, useNavigate } from "react-router-dom";
import { useDbSchema } from "../hooks/useSchema";
import type { DbTableInfo } from "@sdai/types";

// ── Constants ─────────────────────────────────────────────────────────────────

const BASE_COLS = new Set([
  "id", "source_image_path", "batch_id", "ingested_at", "confidence", "review_status",
]);

const NODE_WIDTH_MIN  = 220;
const NODE_COL_HEIGHT = 22;
const NODE_HEADER_H   = 52;
const NODE_FOOTER_H   = 30;
const MAX_VISIBLE_COLS = 10;

// ── Custom node ───────────────────────────────────────────────────────────────

interface TableNodeData {
  table:     DbTableInfo;
  nodeWidth: number;
  [key: string]: unknown;
}

function TableNode({ data, selected }: NodeProps<Node<TableNodeData>>) {
  const { table, nodeWidth } = data;
  const visibleCols = table.columns.slice(0, MAX_VISIBLE_COLS);
  const overflow    = table.columns.length - visibleCols.length;

  return (
    <div
      className={`rounded-lg border bg-white shadow-sm text-xs flex flex-col overflow-hidden transition-shadow ${
        selected ? "border-indigo-500 shadow-indigo-200 shadow-lg" : "border-gray-300 hover:border-gray-400"
      }`}
      style={{ width: nodeWidth }}
    >
      <Handle type="target" position={Position.Left}  style={{ background: "#6366f1", width: 8, height: 8 }} />
      <Handle type="source" position={Position.Right} style={{ background: "#6366f1", width: 8, height: 8 }} />

      {/* Header */}
      <div className="bg-indigo-600 text-white px-3 py-2 flex-shrink-0">
        <div className="font-bold truncate leading-tight">{table.name}</div>
        <div className="text-indigo-200 text-[10px] truncate mt-0.5">{table.document_type}</div>
      </div>

      {/* Column list */}
      <div className="flex-1 divide-y divide-gray-50 px-0">
        {visibleCols.map((col) => (
          <div
            key={col.name}
            className={`flex justify-between items-center px-3 py-[3px] ${
              BASE_COLS.has(col.name) ? "text-gray-400" : "text-gray-700"
            }`}
          >
            <span className="truncate mr-2 font-mono">{col.name}</span>
            <span className="text-gray-400 font-mono shrink-0 text-[10px]">
              {col.type.replace("character varying", "varchar")}
            </span>
          </div>
        ))}
        {overflow > 0 && (
          <div className="px-3 py-[3px] text-gray-400 italic">
            +{overflow} more columns
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-gray-100 px-3 py-1.5 text-gray-500 bg-gray-50 flex-shrink-0 font-medium">
        {table.row_count.toLocaleString()} documents
      </div>
    </div>
  );
}

const nodeTypes = { tableNode: TableNode };

// ── Dagre layout ──────────────────────────────────────────────────────────────

function buildLayout(tables: DbTableInfo[]): { nodes: Node[]; edges: Edge[] } {
  const g = new Dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 60, ranksep: 120 });
  g.setDefaultEdgeLabel(() => ({}));

  const dims: Record<string, { w: number; h: number }> = {};

  tables.forEach((t) => {
    const w = Math.max(NODE_WIDTH_MIN, NODE_WIDTH_MIN + 20 * Math.log1p(t.row_count));
    const visibleCols = Math.min(t.columns.length, MAX_VISIBLE_COLS + (t.columns.length > MAX_VISIBLE_COLS ? 1 : 0));
    const h = NODE_HEADER_H + visibleCols * NODE_COL_HEIGHT + NODE_FOOTER_H;
    dims[t.name] = { w, h };
    g.setNode(t.name, { width: w, height: h });
  });

  const edges: Edge[] = [];
  tables.forEach((t) => {
    t.foreign_keys.forEach((fk) => {
      if (!dims[fk.references_table]) return; // skip if target table not in scope
      const id = `${t.name}__${fk.column}__${fk.references_table}`;
      g.setEdge(t.name, fk.references_table);
      edges.push({
        id,
        source:    t.name,
        target:    fk.references_table,
        label:     fk.column,
        markerEnd: { type: MarkerType.ArrowClosed, color: "#6366f1", width: 16, height: 16 },
        style:     { stroke: "#6366f1", strokeWidth: 1.5 },
        labelStyle:   { fontSize: 10, fill: "#6b7280" },
        labelBgStyle: { fill: "#f9fafb", fillOpacity: 0.8 },
      });
    });
  });

  Dagre.layout(g);

  const nodes: Node[] = tables.map((t) => {
    const pos = g.node(t.name);
    const { w } = dims[t.name];
    return {
      id:       t.name,
      type:     "tableNode",
      position: { x: pos.x - pos.width / 2, y: pos.y - pos.height / 2 },
      data:     { table: t, nodeWidth: w } as TableNodeData,
    };
  });

  return { nodes, edges };
}

// ── Side panel ────────────────────────────────────────────────────────────────

function SidePanel({ table, onClose }: { table: DbTableInfo; onClose: () => void }) {
  return (
    <aside className="w-72 border-l border-gray-200 bg-white flex flex-col overflow-hidden flex-shrink-0">
      <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between flex-shrink-0">
        <span className="text-sm font-bold text-gray-800 truncate">{table.name}</span>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-700 ml-2 flex-shrink-0">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="px-4 py-3 border-b border-gray-200 space-y-1 flex-shrink-0">
        <div className="text-xs text-gray-500">Document type</div>
        <div className="text-sm font-medium text-gray-800">{table.document_type}</div>
      </div>

      <div className="px-4 py-3 border-b border-gray-200 flex gap-6 flex-shrink-0">
        <div>
          <div className="text-xs text-gray-500">Rows</div>
          <div className="text-sm font-semibold text-gray-800">{table.row_count.toLocaleString()}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">Last ingested</div>
          <div className="text-sm font-medium text-gray-800">
            {table.last_ingested ? new Date(table.last_ingested).toLocaleDateString() : "—"}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="px-4 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wide sticky top-0 bg-white border-b border-gray-100">
          Columns ({table.columns.length})
        </div>
        {table.columns.map((col) => (
          <div
            key={col.name}
            className={`px-4 py-1.5 flex justify-between items-center text-xs border-b border-gray-50 ${
              BASE_COLS.has(col.name) ? "text-gray-400" : "text-gray-700"
            }`}
          >
            <span className="truncate mr-2 font-mono">{col.name}</span>
            <span className="text-gray-400 font-mono shrink-0 text-[10px]">
              {col.type.replace("character varying", "varchar")}
              {!col.nullable && " NOT NULL"}
            </span>
          </div>
        ))}
      </div>

      <div className="px-4 py-3 border-t border-gray-200 flex-shrink-0">
        <Link
          to={`/documents?document_type=${encodeURIComponent(table.name)}`}
          className="block w-full text-center px-3 py-2 rounded-lg bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 transition-colors"
        >
          View documents of this type
        </Link>
      </div>
    </aside>
  );
}

// ── Inner canvas component (needs ReactFlowProvider context) ──────────────────

interface CanvasProps {
  tables:         DbTableInfo[] | undefined;
  loading:        boolean;
  error:          unknown;
  autoRefresh:    boolean;
  setAutoRefresh: (v: boolean) => void;
  onRefresh:      () => void;
}

function SchemaCanvas({ tables, loading, error, autoRefresh, setAutoRefresh, onRefresh }: CanvasProps) {
  const { fitView } = useReactFlow();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selected, setSelected]          = useState<DbTableInfo | null>(null);
  const schemaKeyRef = useRef<string>("");

  useEffect(() => {
    if (!tables) return;
    const key = JSON.stringify(tables);
    if (key === schemaKeyRef.current) return;
    schemaKeyRef.current = key;

    const { nodes: n, edges: e } = buildLayout(tables);
    setNodes(n);
    setEdges(e);
    setTimeout(() => fitView({ padding: 0.15, duration: 400 }), 60);
  }, [tables, fitView, setNodes, setEdges]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    const t = (node.data as TableNodeData).table;
    setSelected((prev) => (prev?.name === t.name ? null : t));
  }, []);

  const onPaneClick = useCallback(() => setSelected(null), []);

  const downloadPng = useCallback(() => {
    const el = document.querySelector<HTMLElement>(".react-flow");
    if (!el) return;
    toPng(el, { backgroundColor: "#f8fafc", pixelRatio: 2 }).then((url) => {
      const a = document.createElement("a");
      a.href     = url;
      a.download = "schema.png";
      a.click();
    });
  }, []);

  const isEmpty = !loading && !error && (tables?.length ?? 0) === 0;

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="bg-white border-b border-gray-200 px-6 py-2 flex items-center gap-3 flex-shrink-0">
        <span className="text-xs text-gray-500">
          {loading ? "Loading…" : error ? "" : `${tables?.length ?? 0} table${(tables?.length ?? 0) !== 1 ? "s" : ""}`}
        </span>

        <div className="flex items-center gap-2 ml-auto">
          {/* Auto-refresh toggle */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <span className="text-xs text-gray-600">Auto-refresh</span>
            <button
              role="switch"
              aria-checked={autoRefresh}
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors focus:outline-none ${
                autoRefresh ? "bg-indigo-600" : "bg-gray-300"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 rounded-full bg-white shadow ring-0 transition-transform ${
                  autoRefresh ? "translate-x-4" : "translate-x-0"
                }`}
              />
            </button>
          </label>

          <button
            onClick={onRefresh}
            className="px-3 py-1.5 rounded-md text-xs border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            Refresh
          </button>

          <button
            onClick={() => fitView({ padding: 0.15, duration: 400 })}
            className="px-3 py-1.5 rounded-md text-xs border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            Fit view
          </button>

          <button
            onClick={downloadPng}
            className="px-3 py-1.5 rounded-md text-xs border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            Download PNG
          </button>
        </div>
      </div>

      {/* Main area */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 relative">
          {isEmpty ? (
            <div className="flex h-full flex-col items-center justify-center gap-4">
              <p className="text-sm text-gray-400">No documents ingested yet</p>
              <Link
                to="/upload"
                className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 transition-colors"
              >
                Upload documents
              </Link>
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={onNodeClick}
              onPaneClick={onPaneClick}
              nodeTypes={nodeTypes}
              fitView
              minZoom={0.1}
              maxZoom={4}
              proOptions={{ hideAttribution: false }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#e2e8f0" />
              <MiniMap
                nodeColor={(node) => {
                  const t = (node.data as TableNodeData).table;
                  if (t.row_count > 500) return "#4f46e5";
                  if (t.row_count > 50)  return "#6366f1";
                  if (t.row_count > 5)   return "#818cf8";
                  return "#a5b4fc";
                }}
                maskColor="rgba(248,250,252,0.85)"
                className="!bottom-4 !right-4"
              />
            </ReactFlow>
          )}
        </div>

        {/* Slide-in side panel */}
        {selected && <SidePanel table={selected} onClose={() => setSelected(null)} />}
      </div>
    </div>
  );
}

// ── Page shell ────────────────────────────────────────────────────────────────

export default function SchemaPage() {
  const navigate                               = useNavigate();
  const [autoRefresh, setAutoRefresh]          = useState(false);
  const { data, isLoading, error, refetch }    = useDbSchema({ autoRefresh });

  return (
    <div className="flex flex-col h-screen bg-slate-50 font-sans text-gray-900">
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4 flex-shrink-0">
        <button
          onClick={() => navigate("/")}
          className="text-gray-500 hover:text-gray-800 transition-colors"
          aria-label="Back to dashboard"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
        </button>
        <h1 className="text-base font-bold text-indigo-700 tracking-tight">Schema Visualisation</h1>
        {error && <span className="text-xs text-red-500">Failed to load schema</span>}
      </header>

      <div className="flex-1 overflow-hidden">
        <ReactFlowProvider>
          <SchemaCanvas
            tables={data?.tables}
            loading={isLoading}
            error={error}
            autoRefresh={autoRefresh}
            setAutoRefresh={setAutoRefresh}
            onRefresh={() => { void refetch(); }}
          />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
