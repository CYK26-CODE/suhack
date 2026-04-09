const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export const api = {
  getRunStatus: (runId: string) =>
    fetch(`${BASE}/pipeline/${runId}`).then((r) => r.json()),

  getRiskScores: (runId: string) =>
    fetch(`${BASE}/predict/risk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    }).then((r) => r.json()),

  getExplainability: (runId: string) =>
    fetch(`${BASE}/predict/explain/${runId}`).then((r) => r.json()),

  triggerPipeline: (repoUrl: string, branch: string) =>
    fetch(`${BASE}/pipeline/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_url: repoUrl, branch }),
    }).then((r) => r.json()),

  analyzeRepo: (repoUrl: string, branch: string = "main") =>
    fetch(`${BASE}/analyze/repo?repo_url=${encodeURIComponent(repoUrl)}&branch=${branch}`).then(
      (r) => r.json()
    ),

  getComplexity: (runId: string) =>
    fetch(`${BASE}/analyze/complexity`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    }).then((r) => r.json()),
};

