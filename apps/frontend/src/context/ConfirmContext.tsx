import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface ConfirmOptions {
  message: string;
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

type ConfirmInput = ConfirmOptions | string;

interface ConfirmContextValue {
  confirm: (options: ConfirmInput) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null);

// A styled stand-in for window.confirm() - same call-and-await shape
// (`if (!(await confirm(message))) return;`), but rendered in-app instead
// of the browser's native dialog. One instance mounted at the app root;
// callers never touch the modal directly, just the returned promise.
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolveRef = useRef<((value: boolean) => void) | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  const confirm = useCallback((input: ConfirmInput) => {
    const opts = typeof input === "string" ? { message: input } : input;
    setOptions(opts);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
    });
  }, []);

  const settle = useCallback((result: boolean) => {
    setOptions(null);
    resolveRef.current?.(result);
    resolveRef.current = null;
  }, []);

  useEffect(() => {
    if (!options) return;
    confirmButtonRef.current?.focus();
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") settle(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [options, settle]);

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {options && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center px-4">
          <div
            className="absolute inset-0 bg-gray-900/40"
            onClick={() => settle(false)}
          />
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-dialog-message"
            className="relative w-full max-w-sm rounded-xl bg-white shadow-xl border border-gray-200 p-5"
          >
            {options.title && (
              <h2 className="text-sm font-bold text-gray-900 mb-1.5">{options.title}</h2>
            )}
            <p id="confirm-dialog-message" className="text-sm text-gray-600 whitespace-pre-wrap">
              {options.message}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => settle(false)}
                className="px-3 py-1.5 rounded-lg text-sm font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
              >
                {options.cancelLabel ?? t("confirmDialog.cancel")}
              </button>
              <button
                ref={confirmButtonRef}
                onClick={() => settle(true)}
                className={`px-3 py-1.5 rounded-lg text-sm font-semibold text-white transition-colors ${options.danger
                    ? "bg-red-600 hover:bg-red-700"
                    : "bg-indigo-600 hover:bg-indigo-700"
                  }`}
              >
                {options.confirmLabel ?? t("confirmDialog.confirm")}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): (options: ConfirmInput) => Promise<boolean> {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used inside <ConfirmProvider>");
  return ctx.confirm;
}
