import { useEffect, useState } from "react";
import { api } from "./api";
import CaseList from "./components/CaseList";
import CaseDetail from "./components/CaseDetail";
import "./app.css";

export default function App() {
  const [stages, setStages] = useState({});
  const [stage, setStage] = useState("warehouse");
  const [fingerprints, setFingerprints] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [fatalError, setFatalError] = useState(null);

  useEffect(() => {
    api
      .stages()
      .then(setStages)
      .catch((e) => setFatalError(e.message));
  }, []);

  useEffect(() => {
    setLoading(true);
    setSelectedId(null);
    api
      .fingerprints(stage)
      .then((fps) => {
        setFingerprints(fps);
        if (fps.length > 0) setSelectedId(fps[0].fingerprint_id);
      })
      .catch((e) => setFatalError(e.message))
      .finally(() => setLoading(false));
  }, [stage]);

  if (fatalError) {
    return (
      <div className="fatal-error">
        <div className="eyebrow" style={{ color: "var(--refuted)" }}>
          Could not reach the API
        </div>
        <p>{fatalError}</p>
        <p className="fatal-error__hint">
          Confirm the backend is running (<span className="mono">uvicorn app.main:app</span>) and the test bed has
          been generated (<span className="mono">python -m data_gen.run_generate --out csv</span>).
        </p>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <CaseList
        stage={stage}
        stages={stages}
        onStageChange={setStage}
        fingerprints={fingerprints}
        loading={loading}
        selectedId={selectedId}
        onSelect={setSelectedId}
      />
      <CaseDetail fingerprintId={selectedId} stage={stage} />
    </div>
  );
}
