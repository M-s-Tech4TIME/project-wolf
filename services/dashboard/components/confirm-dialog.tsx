"use client";

import { AlertTriangle } from "lucide-react";
import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";

type Props = {
  open: boolean;
  title: string;
  /** Body — supply React nodes so callers can mix string + emphasis. */
  description: React.ReactNode;
  /** Defaults: "Delete" for destructive flow, "Confirm" otherwise. */
  confirmLabel?: string;
  cancelLabel?: string;
  /** "destructive" colours the confirm button red and primes the dialog
   *  with the warning icon. Anything else keeps it neutral. */
  variant?: "destructive" | "default";
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Slice 5.0c-i.3: app-native confirmation dialog used in place of the
 * browser-native `window.confirm` for destructive flows (currently:
 * single-conversation Delete in the sidebar, bulk Delete from the
 * chats-history overlay).
 *
 * Built without @radix-ui/react-dialog to keep the dependency
 * footprint flat for now — a plain fixed overlay + backdrop + card.
 * Esc dismisses; clicking the backdrop dismisses; the Cancel button
 * receives initial focus (safer default than the destructive
 * confirm button).
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  variant = "default",
  onConfirm,
  onCancel,
}: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Focus the cancel button on open so an accidental Enter doesn't
  // confirm a destructive action.
  useEffect(() => {
    if (open) requestAnimationFrame(() => cancelRef.current?.focus());
  }, [open]);

  // Esc to dismiss.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open) return null;

  const resolvedConfirmLabel =
    confirmLabel ?? (variant === "destructive" ? "Delete" : "Confirm");

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      className="fixed inset-0 z-[60] flex items-center justify-center animate-in fade-in-0 duration-150"
    >
      {/* Backdrop — click to dismiss */}
      <button
        type="button"
        aria-label="Dismiss dialog"
        tabIndex={-1}
        onClick={onCancel}
        className="absolute inset-0 cursor-default bg-foreground/40 backdrop-blur-sm"
      />
      {/* Card */}
      <div className="relative z-10 w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-lg animate-in zoom-in-95 duration-150">
        <div className="flex items-start gap-3">
          {variant === "destructive" ? (
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-destructive/15 text-destructive">
              <AlertTriangle className="h-5 w-5" />
            </div>
          ) : null}
          <div className="min-w-0 flex-1">
            <h3
              id="confirm-dialog-title"
              className="text-sm font-semibold tracking-tight"
            >
              {title}
            </h3>
            <div className="mt-1 text-sm text-muted-foreground">
              {description}
            </div>
          </div>
        </div>
        <div className="mt-5 flex items-center justify-end gap-2">
          <Button
            ref={cancelRef}
            variant="ghost"
            size="sm"
            onClick={onCancel}
          >
            {cancelLabel}
          </Button>
          <Button
            variant={variant === "destructive" ? "destructive" : "default"}
            size="sm"
            onClick={onConfirm}
          >
            {resolvedConfirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
