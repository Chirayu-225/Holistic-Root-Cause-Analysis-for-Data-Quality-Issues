const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request(path) {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  stages: () => request("/api/stages"),
  fingerprints: (stage) => request(`/api/fingerprints?stage=${encodeURIComponent(stage)}`),
  report: (fingerprintId, stage) =>
    request(`/api/fingerprints/${encodeURIComponent(fingerprintId)}/report?stage=${encodeURIComponent(stage)}&run_llm=false`),
  queryLogMining: () => request("/api/lineage/query-log-mining"),
};
