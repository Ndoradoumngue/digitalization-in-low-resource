import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ImageVariant } from "@sdai/types";
import { imageUrl } from "@sdai/api-client";

interface Props {
  filename: string;
}

export default function ImageViewer({ filename }: Props) {
  const { t } = useTranslation();
  const [variant, setVariant] = useState<ImageVariant>("raw");
  const [error, setError] = useState(false);

  const src = imageUrl(filename, variant);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-gray-600">{t("imageViewer.variantLabel")}</span>
        {(["raw", "preprocessed"] as ImageVariant[]).map((v) => (
          <button
            key={v}
            onClick={() => { setVariant(v); setError(false); }}
            className={`px-3 py-1 rounded-full text-xs font-semibold transition-colors ${
              variant === v
                ? "bg-indigo-600 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            {v === "raw" ? t("imageViewer.raw") : t("imageViewer.preprocessed")}
          </button>
        ))}
      </div>

      <div className="relative border border-gray-200 rounded-lg overflow-hidden bg-gray-50 min-h-64 flex items-center justify-center">
        {error ? (
          <div className="text-sm text-gray-400 p-6 text-center">
            {t("imageViewer.notAvailable", { variant })}
          </div>
        ) : (
          <img
            key={src}
            src={src}
            alt={`${filename} — ${variant}`}
            className="max-w-full max-h-[600px] object-contain"
            onError={() => setError(true)}
          />
        )}
      </div>
    </div>
  );
}
