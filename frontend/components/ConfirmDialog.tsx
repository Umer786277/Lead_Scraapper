"use client";

import { AlertTriangle, CheckCircle2, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "danger" | "primary";
  showCancel?: boolean;
  onConfirm: () => Promise<void | boolean>;
  onError: (error: unknown) => void;
  onClose: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  tone = "danger",
  showCancel = true,
  onConfirm,
  onError,
  onClose,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [submitting, setSubmitting] = useState(false);
  const titleId = "app-confirm-dialog-title";
  const descriptionId = "app-confirm-dialog-description";

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  async function handleConfirm() {
    if (submitting) return;
    setSubmitting(true);
    try {
      const shouldClose = await onConfirm();
      if (shouldClose !== false) onClose();
    } catch (error) {
      onError(error);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        if (!submitting) onClose();
      }}
      onClick={(event) => {
        if (event.target === dialogRef.current && !submitting) onClose();
      }}
      className="m-auto w-[calc(100%-2rem)] max-w-lg rounded-2xl border border-white/10 bg-slate-950 p-0 text-white shadow-2xl backdrop:bg-slate-950/75 backdrop:backdrop-blur-sm"
    >
      {open && (
        <div className="p-6 sm:p-7">
          <div className="flex items-start gap-4">
            <span className={`flex size-11 shrink-0 items-center justify-center rounded-xl ring-1 ring-inset ${
              tone === "danger"
                ? "bg-red-400/10 text-red-300 ring-red-400/20"
                : "bg-indigo-400/10 text-indigo-300 ring-indigo-400/20"
            }`}>
              {tone === "danger" ? <AlertTriangle size={21} /> : <CheckCircle2 size={21} />}
            </span>
            <div className="min-w-0">
              <h2 id={titleId} className="text-lg font-semibold tracking-tight">{title}</h2>
              <p id={descriptionId} className="mt-2 break-words text-sm leading-6 text-slate-400">
                {description}
              </p>
            </div>
          </div>

          <div className="mt-7 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            {showCancel && (
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="rounded-xl border border-white/10 px-4 py-2.5 text-sm font-medium text-slate-300 transition hover:bg-white/5 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {cancelLabel}
              </button>
            )}
            <button
              type="button"
              onClick={() => void handleConfirm()}
              disabled={submitting}
              className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white shadow-lg transition focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-wait disabled:opacity-60 ${
                tone === "danger"
                  ? "bg-red-500 shadow-red-950/30 hover:bg-red-400 focus-visible:outline-red-300"
                  : "bg-indigo-600 shadow-indigo-950/30 hover:bg-indigo-500 focus-visible:outline-indigo-300"
              }`}
            >
              {submitting && <LoaderCircle size={16} className="animate-spin" />}
              {submitting ? "Please wait…" : confirmLabel}
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}
