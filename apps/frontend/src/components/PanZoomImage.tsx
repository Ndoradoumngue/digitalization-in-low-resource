import { useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";

export default function PanZoomImage({ src }: { src: string }) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const [scale, setScale]   = useState(1);
  const [tx, setTx]         = useState(0);
  const [ty, setTy]         = useState(0);
  const dragRef = useRef<{ startX: number; startY: number; tx: number; ty: number } | null>(null);

  const reset = useCallback(() => { setScale(1); setTx(0); setTy(0); }, []);

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setScale((s) => Math.min(8, Math.max(0.25, s * (e.deltaY < 0 ? 1.15 : 1 / 1.15))));
  }, []);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return;
    dragRef.current = { startX: e.clientX, startY: e.clientY, tx, ty };
  }, [tx, ty]);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragRef.current) return;
    const { startX, startY, tx: ox, ty: oy } = dragRef.current;
    setTx(ox + e.clientX - startX);
    setTy(oy + e.clientY - startY);
  }, []);

  const onMouseUp = useCallback(() => { dragRef.current = null; }, []);

  // A file the database references but this server does not have yet
  // (local/cloud sync still copying it) answers 404 with X-Sync-Pending -
  // say so instead of showing a broken image.
  const [failure, setFailure] = useState<"pending" | "missing" | null>(null);
  const onError = useCallback(() => {
    fetch(src, { credentials: "include" })
      .then((res) => setFailure(res.headers.get("X-Sync-Pending") ? "pending" : "missing"))
      .catch(() => setFailure("missing"));
  }, [src]);

  if (failure) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-gray-800 text-gray-300 text-sm p-6 text-center">
        {failure === "pending" ? t("sync.filePending") : t("panZoom.notAvailable")}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden bg-gray-800 select-none"
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
      onDoubleClick={reset}
      style={{ cursor: dragRef.current ? "grabbing" : "grab" }}
    >
      <img
        src={src}
        alt={t("panZoom.alt")}
        draggable={false}
        onError={onError}
        className="absolute top-1/2 left-1/2 max-w-none"
        style={{
          transform: `translate(calc(-50% + ${tx}px), calc(-50% + ${ty}px)) scale(${scale})`,
          transformOrigin: "center center",
        }}
      />
      <div className="absolute bottom-3 right-3 bg-black/50 text-white text-xs px-2 py-1 rounded pointer-events-none">
        {t("panZoom.overlay", { pct: Math.round(scale * 100) })}
      </div>
    </div>
  );
}
