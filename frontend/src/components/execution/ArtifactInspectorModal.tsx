interface Props {
  open: boolean;
  title: string;
  content: string;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

export default function ArtifactInspectorModal({
  open,
  title,
  content,
  loading,
  error,
  onClose,
}: Props) {
  if (!open) return null;

  return (
    <div className="artifact-overlay" role="presentation" onClick={onClose}>
      <div
        className="artifact-inspector"
        role="dialog"
        aria-modal="true"
        aria-labelledby="artifact-inspector-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="artifact-inspector__header">
          <h3 id="artifact-inspector-title">{title}</h3>
          <button type="button" className="btn btn--sm" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <div className="artifact-inspector__body">
          {loading ? <p className="artifact-inspector__status">Loading artifact…</p> : null}
          {error ? <p className="artifact-inspector__error">{error}</p> : null}
          {!loading && !error ? (
            <pre className="artifact-inspector__json">{content}</pre>
          ) : null}
        </div>
      </div>
    </div>
  );
}
