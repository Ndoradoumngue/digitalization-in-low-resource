import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { OcrDocument, ImageVariant } from "@sdai/types";
import { ENGINE_LABELS } from "@sdai/types";
import EngineCard from "./EngineCard";
import ImageViewer from "./ImageViewer";

interface Props {
  doc: OcrDocument;
}

export default function OcrPanel({ doc }: Props) {
  const { t } = useTranslation();
  const [variant, setVariant] = useState<ImageVariant>("raw");
  const variantData = doc[variant];

  const bestEngine = (["tesseract", "easyocr", "surya"] as const).reduce(
    (best, eng) =>
      variantData[eng].confidence > variantData[best].confidence ? eng : best,
    "tesseract" as const
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium text-gray-600">{t("ocr.resultsFor")}</span>
        {(["raw", "preprocessed"] as ImageVariant[]).map((v) => (
          <button
            key={v}
            onClick={() => setVariant(v)}
            className={`px-4 py-1.5 rounded-full text-sm font-semibold transition-colors ${
              variant === v
                ? "bg-indigo-600 text-white shadow-sm"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            {v === "raw" ? t("ocr.rawImage") : t("ocr.preprocessedImage")}
          </button>
        ))}
        <span className="ml-auto text-xs text-gray-400">
          {t("ocr.bestEngine")}{" "}
          <span className="font-semibold text-indigo-600">
            {ENGINE_LABELS[bestEngine]}
          </span>
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <div>
          <h3 className="text-sm font-medium text-gray-500 mb-2">{t("ocr.documentImage")}</h3>
          <ImageViewer filename={doc.filename} />
        </div>

        <div className="flex flex-col gap-4">
          <h3 className="text-sm font-medium text-gray-500">{t("ocr.extractionResults")}</h3>
          {(["tesseract", "easyocr", "surya"] as const).map((eng) => (
            <EngineCard
              key={eng}
              label={ENGINE_LABELS[eng]}
              result={variantData[eng]}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
