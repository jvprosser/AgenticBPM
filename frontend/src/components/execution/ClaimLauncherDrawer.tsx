import { useEffect, useState } from "react";

interface Props {
  open: boolean;
  inputParameter: string;
  onClose: () => void;
  onSubmit: (claimNumber: string, parameters: Record<string, string>) => Promise<void>;
}

export default function ClaimLauncherDrawer({
  open,
  inputParameter,
  onClose,
  onSubmit,
}: Props) {
  const [claimNumber, setClaimNumber] = useState("");
  const [parameterValue, setParameterValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setClaimNumber("");
    setParameterValue("");
    setError(null);
  }, [open]);

  if (!open) return null;

  const paramKey = inputParameter.trim() || "claim_id";
  const paramLabel = inputParameter.trim() || "claim_id";

  const handleSubmit = async () => {
    const number = claimNumber.trim();
    if (!number) {
      setError("Claim number is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const parameters: Record<string, string> = { [paramKey]: parameterValue.trim() || number };
      await onSubmit(number, parameters);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="claim-drawer-overlay" role="presentation" onClick={onClose}>
      <aside
        className="claim-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="claim-drawer-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="claim-drawer__header">
          <h3 id="claim-drawer-title">Run New Claim</h3>
          <button type="button" className="btn btn--sm" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <div className="claim-drawer__body">
          <label className="metadata-field">
            <span>Claim Number</span>
            <input
              type="text"
              value={claimNumber}
              placeholder='e.g. "CLM-2026-8819"'
              onChange={(e) => setClaimNumber(e.target.value)}
            />
          </label>
          <label className="metadata-field">
            <span>{paramLabel}</span>
            <input
              type="text"
              value={parameterValue}
              placeholder={`Value for ${paramLabel}`}
              onChange={(e) => setParameterValue(e.target.value)}
            />
          </label>
          {error ? <p className="claim-drawer__error">{error}</p> : null}
        </div>
        <footer className="claim-drawer__footer">
          <button type="button" className="btn btn--sm" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn--agentic btn--sm" onClick={() => void handleSubmit()} disabled={busy}>
            {busy ? "Launching…" : "Launch Run"}
          </button>
        </footer>
      </aside>
    </div>
  );
}
