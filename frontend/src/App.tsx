import { useEffect, useState } from "react";
import Dropzone from "./components/Dropzone";
import ProcessCanvas from "./components/ProcessCanvas";
import { getHealth, type HealthResult } from "./api";

export default function App() {
  const [health, setHealth] = useState<HealthResult | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [processId, setProcessId] = useState<string | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealthError(true));
  }, []);

  return (
    <div className={processId ? "app app--wide" : "app"}>
      <header className="app__header">
        <h1>Cloudera AI Process Mapper</h1>
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
      </header>

      <main className="app__main">
        {processId ? (
          <ProcessCanvas processId={processId} onReset={() => setProcessId(null)} />
        ) : (
          <>
            <p className="lede">
              Upload a BPMN file. The backend parses it into a graph, lays it out, and
              stores it in SQLite — then it renders on an editable canvas below.
            </p>
            <Dropzone onIngested={setProcessId} />
          </>
        )}
      </main>
    </div>
  );
}
