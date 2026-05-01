import React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { credentialKeySchema, type CredentialKeyForm } from "../lib/schemas/dispatch";
import { SaveIndicator, type SaveState } from "./SaveIndicator";

export interface CredentialKeyModalProps {
  open: boolean;
  providerLabel: string;
  onSubmit: (key: string) => Promise<void>;
  onClose: () => void;
}

export const CredentialKeyModal: React.FC<CredentialKeyModalProps> = ({
  open,
  providerLabel,
  onSubmit,
  onClose,
}) => {
  const [saveState, setSaveState] = React.useState<SaveState>("idle");

  const {
    register,
    handleSubmit,
    formState: { errors },
    reset,
  } = useForm<CredentialKeyForm>({
    resolver: zodResolver(credentialKeySchema),
    defaultValues: { key: "" },
  });

  React.useEffect(() => {
    if (!open) {
      reset();
      setSaveState("idle");
    }
  }, [open, reset]);

  const submitForm = React.useCallback(
    async (data: CredentialKeyForm) => {
      setSaveState("saving");
      try {
        await onSubmit(data.key);
        setSaveState("saved");
        setTimeout(() => {
          onClose();
          setSaveState("idle");
        }, 800);
      } catch {
        setSaveState("error");
      }
    },
    [onSubmit, onClose],
  );

  if (!open) return null;

  const inputId = "credential-key-input";
  const errorId = errors.key ? "credential-key-error" : undefined;

  return (
    <div
      aria-modal="true"
      aria-label={"Enter API key for " + providerLabel}
      className="credential-key-modal"
      onClick={onClose}
      role="dialog"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15,17,23,0.8)",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
      }}
    >
      <div
        className="credential-key-modal-panel"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          padding: 24,
          maxWidth: 420,
          width: "100%",
        }}
      >
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, color: "var(--text-primary)" }}>
          Enter API key for {providerLabel}
        </h2>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16, lineHeight: 1.45 }}>
          This will update the stored credential for this provider on the server.
        </p>

        <form noValidate onSubmit={handleSubmit(submitForm)}>
          <label
            htmlFor={inputId}
            style={{
              display: "block",
              fontSize: 13,
              fontWeight: 500,
              marginBottom: 6,
              color: "var(--text-primary)",
            }}
          >
            API Key
          </label>
          <input
            {...register("key")}
            aria-describedby={errorId}
            aria-invalid={!!errors.key}
            autoComplete="off"
            autoFocus
            className={errors.key ? "input-invalid" : ""}
            id={inputId}
            placeholder="Paste key here…"
            style={{
              width: "100%",
              padding: "10px 12px",
              fontSize: 14,
              borderRadius: 8,
              border: errors.key
                ? "1px solid var(--accent-red)"
                : "1px solid var(--border)",
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
              marginBottom: 6,
            }}
            type="password"
          />
          {errors.key && (
            <p
              className="input-error"
              id={errorId}
              role="alert"
              style={{
                fontSize: 12,
                color: "var(--accent-red)",
                marginBottom: 12,
              }}
            >
              {errors.key.message}
            </p>
          )}

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginTop: 16,
              gap: 12,
            }}
          >
            <SaveIndicator
              onRetry={() => handleSubmit(submitForm)()}
              state={saveState}
            />
            <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
              <button
                className="btn"
                disabled={saveState === "saving"}
                onClick={onClose}
                style={{ fontSize: 13, padding: "8px 14px" }}
                type="button"
              >
                Cancel
              </button>
              <button
                className="btn btn-blue"
                disabled={saveState === "saving"}
                style={{ fontSize: 13, padding: "8px 14px" }}
                type="submit"
              >
                Save
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
