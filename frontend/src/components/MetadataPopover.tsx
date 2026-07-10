import { useCallback, useEffect, useRef, useState } from "react";
import {
  upsertMetadata,
  type MetadataRecord,
} from "../api";

const DEBOUNCE_MS = 400;

export interface MetadataTarget {
  ownerType: "node" | "group";
  ownerId: string;
  title: string;
  variant?: "default" | "proposed";
  rationale?: string;
}

interface Props {
  processId: string;
  target: MetadataTarget | null;
  initial: MetadataRecord;
  onClose: () => void;
  onSaved: (ownerType: "node" | "group", ownerId: string, meta: MetadataRecord) => void;
}

type SaveState = "idle" | "saving" | "saved" | "error";

export default function MetadataPopover({
  processId,
  target,
  initial,
  onClose,
  onSaved,
}: Props) {
  const [form, setForm] = useState<MetadataRecord>(initial);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef(form);

  useEffect(() => {
    setForm(initial);
    latest.current = initial;
    setSaveState("idle");
  }, [target?.ownerId, target?.ownerType, initial]);

  const persist = useCallback(
    async (payload: MetadataRecord) => {
      if (!target) return;
      setSaveState("saving");
      try {
        const res = await upsertMetadata(processId, {
          owner_type: target.ownerType,
          owner_id: target.ownerId,
          metadata: payload,
        });
        setSaveState("saved");
        onSaved(target.ownerType, target.ownerId, res.metadata);
      } catch {
        setSaveState("error");
      }
    },
    [processId, target, onSaved]
  );

  const scheduleSave = useCallback(
    (next: MetadataRecord) => {
      latest.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void persist(latest.current);
      }, DEBOUNCE_MS);
    },
    [persist]
  );

  const update = <K extends keyof MetadataRecord>(key: K, value: MetadataRecord[K]) => {
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      scheduleSave(next);
      return next;
    });
  };

  if (!target) return null;

  const isProposed = target.variant === "proposed";

  const statusLabel =
    isProposed
      ? ""
      : saveState === "saving"
      ? "Saving…"
      : saveState === "saved"
      ? "Saved"
      : saveState === "error"
      ? "Save failed"
      : "";

  return (
    <aside className="metadata-popover" role="dialog" aria-label="Metadata editor">
      <div className="metadata-popover__header">
        <h3>{target.title}</h3>
        <button type="button" className="btn btn--sm" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      {isProposed ? (
        <>
          <p className="metadata-popover__hint">AI-generated optimization proposal.</p>
          <div className="metadata-field">
            <span>Rationale</span>
            <p className="metadata-rationale">{target.rationale ?? initial.description ?? "—"}</p>
          </div>
          <button type="button" className="btn btn--agentic" disabled title="Available in Step 5d">
            Agentic Options
          </button>
        </>
      ) : (
        <>
          <p className="metadata-popover__hint">
            Changes save automatically ({target.ownerType}).
          </p>

          <label className="metadata-field">
            <span>Name</span>
            <input
              type="text"
              value={form.name ?? ""}
              onChange={(e) => update("name", e.target.value || null)}
            />
          </label>

          <label className="metadata-field">
            <span>Owner</span>
            <input
              type="text"
              value={form.owner ?? ""}
              onChange={(e) => update("owner", e.target.value || null)}
            />
          </label>

          <div className="metadata-field metadata-field--row">
            <label>
              <span>Expected duration</span>
              <input
                type="number"
                min={0}
                value={form.duration_value ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  update("duration_value", v === "" ? null : parseInt(v, 10));
                }}
              />
            </label>
            <label>
              <span>Unit</span>
              <select
                value={form.duration_unit ?? ""}
                onChange={(e) =>
                  update(
                    "duration_unit",
                    (e.target.value || null) as MetadataRecord["duration_unit"]
                  )
                }
              >
                <option value="">—</option>
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </select>
            </label>
          </div>

          <label className="metadata-field">
            <span>Description</span>
            <textarea
              rows={4}
              value={form.description ?? ""}
              onChange={(e) => update("description", e.target.value || null)}
            />
          </label>

          <p className={`metadata-popover__status metadata-popover__status--${saveState}`}>
            {statusLabel}
          </p>
        </>
      )}
    </aside>
  );
}
