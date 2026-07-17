import { useCallback, useEffect, useRef, useState } from "react";
import {
  listProcesses,
  patchProcess,
  type ProcessSummary,
} from "../api";

const DESCRIPTION_DEBOUNCE_MS = 400;

interface Props {
  onLoadProcess: (processId: string) => void;
  refreshKey?: number;
}

function formatRegistryDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export default function ProcessRegistryDashboard({
  onLoadProcess,
  refreshKey = 0,
}: Props) {
  const [processes, setProcesses] = useState<ProcessSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});

  const lastSaved = useRef<Record<string, string>>({});
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const loadRegistry = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listProcesses();
      setProcesses(data.processes);
      const nextDescriptions: Record<string, string> = {};
      for (const row of data.processes) {
        const value = row.description ?? "";
        nextDescriptions[row.id] = value;
        lastSaved.current[row.id] = value;
      }
      setDescriptions(nextDescriptions);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRegistry();
  }, [loadRegistry, refreshKey]);

  useEffect(() => {
    return () => {
      for (const timer of timers.current.values()) clearTimeout(timer);
      timers.current.clear();
    };
  }, []);

  const persistDescription = useCallback(async (processId: string, description: string) => {
    if (lastSaved.current[processId] === description) return;
    try {
      const updated = await patchProcess(processId, { description: description || null });
      lastSaved.current[processId] = description;
      setProcesses((rows) =>
        rows.map((row) =>
          row.id === processId
            ? { ...row, description: updated.description, updated_at: updated.updated_at }
            : row
        )
      );
      setSaveErrors((prev) => {
        const next = { ...prev };
        delete next[processId];
        return next;
      });
    } catch (e) {
      setSaveErrors((prev) => ({
        ...prev,
        [processId]: e instanceof Error ? e.message : String(e),
      }));
    }
  }, []);

  const scheduleDescriptionSave = useCallback(
    (processId: string, description: string) => {
      const existing = timers.current.get(processId);
      if (existing) clearTimeout(existing);
      timers.current.set(
        processId,
        setTimeout(() => {
          timers.current.delete(processId);
          void persistDescription(processId, description);
        }, DESCRIPTION_DEBOUNCE_MS)
      );
    },
    [persistDescription]
  );

  const flushDescriptionSave = useCallback(
    (processId: string) => {
      const existing = timers.current.get(processId);
      if (existing) {
        clearTimeout(existing);
        timers.current.delete(processId);
      }
      void persistDescription(processId, descriptions[processId] ?? "");
    },
    [descriptions, persistDescription]
  );

  const onDescriptionChange = (processId: string, value: string) => {
    setDescriptions((prev) => ({ ...prev, [processId]: value }));
    scheduleDescriptionSave(processId, value);
  };

  return (
    <section className="registry">
      <div className="registry__header">
        <h2 className="registry__title">Enterprise Process Registry</h2>
        <p className="registry__subtitle">
          Saved BPMN processes with upload history, editable descriptions, and one-click
          canvas restore.
        </p>
      </div>

      {loading && <p className="registry__status">Loading registry…</p>}
      {loadError && <div className="alert alert--error">{loadError}</div>}

      {!loading && !loadError && processes.length === 0 && (
        <div className="registry__empty">
          Enterprise process registry is currently empty. Upload your first BPMN XML file
          below to begin.
        </div>
      )}

      {!loading && !loadError && processes.length > 0 && (
        <div className="registry__table-wrap">
          <table className="registry__table">
            <thead>
              <tr>
                <th>Process Name</th>
                <th>Filename</th>
                <th>Description</th>
                <th>Upload Date</th>
                <th>Last Saved</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {processes.map((row) => (
                <tr key={row.id}>
                  <td className="registry__cell-name">{row.process_name}</td>
                  <td className="registry__cell-filename">{row.filename}</td>
                  <td className="registry__cell-description">
                    <input
                      type="text"
                      className="registry__description-input"
                      value={descriptions[row.id] ?? ""}
                      placeholder="Add a summary…"
                      onChange={(e) => onDescriptionChange(row.id, e.target.value)}
                      onBlur={() => flushDescriptionSave(row.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          (e.target as HTMLInputElement).blur();
                        }
                      }}
                    />
                    {saveErrors[row.id] && (
                      <span className="registry__save-error">{saveErrors[row.id]}</span>
                    )}
                  </td>
                  <td>{formatRegistryDate(row.created_at)}</td>
                  <td>{formatRegistryDate(row.updated_at)}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn--accent registry__load-btn"
                      onClick={() => onLoadProcess(row.id)}
                    >
                      Load Process Map
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
