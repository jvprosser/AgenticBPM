import { useCallback, useEffect, useState } from "react";
import { getDiscovery, type DiscoveryNamedEntry, type DiscoveryResult } from "../api";

type CatalogTab = "models" | "mcp" | "tools";

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

type LoadState = "idle" | "loading" | "ready" | "error";

const TABS: { id: CatalogTab; label: string }[] = [
  { id: "models", label: "Models" },
  { id: "mcp", label: "MCP Servers" },
  { id: "tools", label: "Available Tools" },
];

function CatalogIcon() {
  return (
    <svg
      className="catalog-btn__icon"
      viewBox="0 0 24 24"
      width="14"
      height="14"
      aria-hidden
    >
      <path
        fill="currentColor"
        d="M6 2a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6H6zm7 1.5L18.5 9H13V3.5zM8 12h8v1.5H8V12zm0 3.5h8V17H8v-1.5z"
      />
    </svg>
  );
}

function ModelList({ models }: { models: string[] }) {
  if (!models.length) {
    return <p className="catalog-empty">No models registered.</p>;
  }
  return (
    <ul className="catalog-grid">
      {models.map((name) => (
        <li key={name} className="catalog-card catalog-card--model">
          <span className="catalog-card__name">{name}</span>
        </li>
      ))}
    </ul>
  );
}

function EntryList({ entries, emptyLabel }: { entries: DiscoveryNamedEntry[]; emptyLabel: string }) {
  if (!entries.length) {
    return <p className="catalog-empty">{emptyLabel}</p>;
  }
  return (
    <ul className="catalog-list">
      {entries.map((entry) => (
        <li key={entry.name} className="catalog-card catalog-card--entry">
          <span className="catalog-card__name">{entry.name}</span>
          {entry.description ? (
            <p className="catalog-card__desc">{entry.description}</p>
          ) : (
            <p className="catalog-card__desc catalog-card__desc--muted">No description provided.</p>
          )}
        </li>
      ))}
    </ul>
  );
}

export function CatalogButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      className="btn btn--catalog"
      onClick={onClick}
      title="View Cloudera AI platform models, MCP servers, and tools"
    >
      <CatalogIcon />
      Platform Catalog
    </button>
  );
}

export default function CatalogDialog({ isOpen, onClose }: Props) {
  const [tab, setTab] = useState<CatalogTab>("models");
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<DiscoveryResult | null>(null);

  const loadCatalog = useCallback(async () => {
    setLoadState("loading");
    setError(null);
    try {
      const result = await getDiscovery();
      setData(result);
      setLoadState("ready");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLoadState("error");
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    setTab("models");
    void loadCatalog();
  }, [isOpen, loadCatalog]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (evt: KeyboardEvent) => {
      if (evt.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const statusBadge = data?.discovery_active ? (
    <span className="catalog-status catalog-status--connected">● Connected</span>
  ) : (
    <span className="catalog-status catalog-status--sandbox">● Sandbox Mode</span>
  );

  return (
    <div className="catalog-overlay" role="presentation" onClick={onClose}>
      <div
        className="catalog-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="catalog-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="catalog-dialog__header">
          <div>
            <h2 id="catalog-dialog-title">Cloudera AI Infrastructure Catalog</h2>
            {loadState === "ready" && statusBadge}
          </div>
          <button
            type="button"
            className="btn btn--sm catalog-dialog__close-x"
            onClick={onClose}
            aria-label="Close catalog"
          >
            ×
          </button>
        </header>

        <nav className="catalog-tabs" aria-label="Catalog sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`catalog-tabs__btn${tab === t.id ? " catalog-tabs__btn--active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <div className="catalog-dialog__body">
          {loadState === "loading" && (
            <p className="catalog-loading">Loading platform catalog…</p>
          )}
          {loadState === "error" && (
            <div className="alert alert--error">
              Failed to load catalog: {error}
              <button type="button" className="btn catalog-retry" onClick={() => void loadCatalog()}>
                Retry
              </button>
            </div>
          )}
          {loadState === "ready" && data && (
            <>
              {!data.discovery_active && data.degraded_reason && (
                <p className="catalog-degraded">{data.degraded_reason}</p>
              )}
              {tab === "models" && <ModelList models={data.models} />}
              {tab === "mcp" && (
                <EntryList entries={data.mcp_servers} emptyLabel="No MCP servers registered." />
              )}
              {tab === "tools" && (
                <EntryList entries={data.tools} emptyLabel="No tools registered." />
              )}
            </>
          )}
        </div>

        <footer className="catalog-dialog__footer">
          <span className="catalog-source">
            {data ? `Source: ${data.source}` : ""}
          </span>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}
