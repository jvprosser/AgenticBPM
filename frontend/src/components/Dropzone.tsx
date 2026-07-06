import { useCallback, useRef, useState } from "react";
import { uploadProcessFile } from "../api";

const ACCEPT = ".xml,.bpmn,.xpdl";

interface Props {
  onIngested: (processId: string) => void;
}

export default function Dropzone({ onIngested }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFile = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      try {
        const result = await uploadProcessFile(file);
        onIngested(result.process_id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [onIngested]
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) void handleFile(file);
    },
    [handleFile]
  );

  return (
    <section>
      <div
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
        <p className="dropzone__title">
          {busy ? "Uploading…" : "Drop a BPMN / XPDL file here"}
        </p>
        <p className="dropzone__hint">or click to browse ({ACCEPT})</p>
      </div>

      {error && <div className="alert alert--error">{error}</div>}
    </section>
  );
}
