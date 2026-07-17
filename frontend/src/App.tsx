import { useEffect, useState } from "react";
import Dropzone from "./components/Dropzone";
import ProcessCanvas from "./components/ProcessCanvas";
import ProcessRegistryDashboard from "./components/ProcessRegistryDashboard";
import CatalogDialog, { CatalogButton } from "./components/CatalogDialog";
import { getHealth, type HealthResult } from "./api";

export default function App() {
  const [health, setHealth] = useState<HealthResult | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [processId, setProcessId] = useState<string | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [registryRefresh, setRegistryRefresh] = useState(0);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealthError(true));
  }, []);

  const handleIngested = (id: string) => {
    setProcessId(id);
  };

  const handleReset = () => {
    setProcessId(null);
    setRegistryRefresh((key) => key + 1);
  };

  return (
    <div className={processId ? "app app--wide" : "app app--registry"}>
      <header className="app__header">
        <h1>Cloudera AI Process Mapper</h1>
        <div className="app__header-actions">
          <CatalogButton onClick={() => setCatalogOpen(true)} />
          <span
            className={`badge ${health ? "badge--ok" : healthError ? "badge--err" : ""}`}
            title="Backend health"
          >
            {health
              ? `backend ok · v${health.version}`
              : healthError
              ? "backend unreachable"
              : "checking…"}
          </span>
        </div>
      </header>

      <main className="app__main">
        {processId ? (
          <ProcessCanvas processId={processId} onReset={handleReset} />
        ) : (
          <>
            <ProcessRegistryDashboard
              refreshKey={registryRefresh}
              onLoadProcess={setProcessId}
            />
            <section className="upload-section">
              <h2 className="upload-section__title">Upload New BPMN Process</h2>
              <p className="lede">
                Drop a BPMN or XPDL file to parse, persist in the registry, and open the
                canvas immediately.
              </p>
              <Dropzone onIngested={handleIngested} />
            </section>
          </>
        )}
      </main>
      <CatalogDialog isOpen={catalogOpen} onClose={() => setCatalogOpen(false)} />
    </div>
  );
}
