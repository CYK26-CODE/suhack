# Module 10 — Frontend Design

## Purpose

The frontend is a Next.js 14 web application that gives operators visibility into pipeline
runs, file risk scores, and the diff between original and healed code. It communicates
exclusively with the FastAPI backend via `NEXT_PUBLIC_API_URL`.

---

## Tech Stack

| Dependency                      | Version | Role                                              |
|---------------------------------|---------|---------------------------------------------------|
| Next.js                         | 14.x    | App Router, React Server Components               |
| React                           | 18.x    | Component rendering                               |
| Tailwind CSS                    | ≥3.4    | Utility-first styling                             |
| react-diff-viewer-continued     | latest  | Side-by-side diff in File Viewer                  |
| recharts                        | ≥2.12   | Risk score bar charts on Dashboard                |
| @tanstack/react-query           | ≥5.40   | Server state, polling, caching                    |
| TypeScript                      | ≥5.4    | Type safety                                       |
| @radix-ui/react-*               | latest  | Accessible UI primitives (tooltips, badges, etc.) |

---

## Pages

### `/` — Dashboard

Shows the most recent pipeline run summary:

- Run ID, repository URL, branch, started/completed timestamps
- Stage status chips (PENDING / RUNNING / COMPLETE / FAILED) for each of the 6 stages
- Summary counts: files analysed, files at HIGH risk, files healed, files validated, PR link
- Bar chart of top 10 files by risk score (recharts)
- Link to the PR if created

Live polling via `useQuery` with `refetchInterval: 3000` while a run is in RUNNING state.

### `/heatmap` — Risk Heatmap

A colour-coded grid of all files in the last analysis:

- Each cell = one file
- Colour = risk level: red (HIGH), amber (MEDIUM), green (LOW), grey (error/unparseable)
- Cell size proportional to `total_churn` (larger = more churn)
- Tooltip on hover shows: file path, risk score, complexity, last modified
- Click a cell → navigates to `/file/[...path]`

### `/file/[...path]` — File Viewer

The primary audit interface. Shows:

- File metadata header: risk score badge, complexity, contributors, last modified
- **Side-by-side diff** (original vs healed) using `react-diff-viewer-continued`
- Heal summary text from the LLM
- Validation results (syntax ✅ / flake8 ✅ / pytest ✅ / complexity delta)
- "View on GitHub" link to the PR if available

Without a diff view, operators have no way to audit what the LLM changed. Showing only the
fixed code is not sufficient.

---

## Setup

```bash
cd frontend
npm install
cp .env.local.example .env.local
# Set NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
npm run dev
```

---

## Key Components

### `PipelineStatusCard`

```tsx
// frontend/components/PipelineStatusCard.tsx
import { useQuery } from "@tanstack/react-query";

type StageStatus = "PENDING" | "RUNNING" | "COMPLETE" | "FAILED" | "SKIPPED";

interface RunContext {
  run_id: string;
  repo_url: string;
  stage_flags: Record<string, StageStatus>;
  pr_url: string | null;
}

const STAGE_ORDER = ["analysis", "complexity", "risk", "healer", "validation", "pr"];

export function PipelineStatusCard({ runId }: { runId: string }) {
  const { data, isLoading } = useQuery<RunContext>({
    queryKey: ["pipeline", runId],
    queryFn: () =>
      fetch(`${process.env.NEXT_PUBLIC_API_URL}/pipeline/${runId}`).then((r) => r.json()),
    refetchInterval: (data) => {
      const flags = data?.stage_flags ?? {};
      const isRunning = Object.values(flags).includes("RUNNING");
      return isRunning ? 3000 : false;
    },
  });

  if (isLoading) return <div className="animate-pulse h-32 bg-gray-100 rounded-lg" />;

  return (
    <div className="rounded-xl border border-gray-200 p-6 shadow-sm">
      <h2 className="text-sm font-mono text-gray-500 mb-4">{runId}</h2>
      <div className="flex gap-2 flex-wrap">
        {STAGE_ORDER.map((stage) => (
          <StageBadge key={stage} stage={stage} status={data?.stage_flags[stage] ?? "PENDING"} />
        ))}
      </div>
      {data?.pr_url && (
        <a
          href={data.pr_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          View PR →
        </a>
      )}
    </div>
  );
}

function StageBadge({ stage, status }: { stage: string; status: StageStatus }) {
  const colours: Record<StageStatus, string> = {
    PENDING:  "bg-gray-100 text-gray-500",
    RUNNING:  "bg-blue-100 text-blue-700 animate-pulse",
    COMPLETE: "bg-green-100 text-green-700",
    FAILED:   "bg-red-100 text-red-700",
    SKIPPED:  "bg-yellow-100 text-yellow-600",
  };
  return (
    <span className={`text-xs font-medium px-2 py-1 rounded-full capitalize ${colours[status]}`}>
      {stage} — {status}
    </span>
  );
}
```

### `RiskHeatmap`

```tsx
// frontend/components/RiskHeatmap.tsx
interface RiskRecord {
  file: string;
  risk_score: number;
  risk_level: "HIGH" | "MEDIUM" | "LOW";
}

function riskColour(level: string) {
  if (level === "HIGH")   return "bg-red-500 hover:bg-red-600";
  if (level === "MEDIUM") return "bg-amber-400 hover:bg-amber-500";
  return "bg-emerald-400 hover:bg-emerald-500";
}

export function RiskHeatmap({ risks }: { risks: RiskRecord[] }) {
  return (
    <div className="flex flex-wrap gap-1 p-4">
      {risks.sort((a, b) => b.risk_score - a.risk_score).map((r) => (
        <a
          key={r.file}
          href={`/file/${r.file}`}
          title={`${r.file}\nScore: ${r.risk_score.toFixed(3)}\nLevel: ${r.risk_level}`}
          className={`block rounded cursor-pointer transition-colors ${riskColour(r.risk_level)}`}
          style={{ width: 24, height: 24 }}
        />
      ))}
    </div>
  );
}
```

### `FileDiffViewer`

```tsx
// frontend/components/FileDiffViewer.tsx
"use client";
import ReactDiffViewer from "react-diff-viewer-continued";

interface Props {
  original: string;
  fixed: string;
  filename: string;
}

export function FileDiffViewer({ original, fixed, filename }: Props) {
  return (
    <div className="rounded-xl overflow-hidden border border-gray-200 shadow-sm">
      <div className="bg-gray-50 px-4 py-2 border-b border-gray-200 font-mono text-sm text-gray-600">
        {filename}
      </div>
      <ReactDiffViewer
        oldValue={original}
        newValue={fixed}
        splitView={true}
        leftTitle="Original"
        rightTitle="Healed"
        useDarkTheme={false}
        hideLineNumbers={false}
      />
    </div>
  );
}
```

---

## API Integration

```typescript
// frontend/lib/api.ts
const BASE = process.env.NEXT_PUBLIC_API_URL!;

export const api = {
  getRunStatus:  (runId: string) => fetch(`${BASE}/pipeline/${runId}`).then(r => r.json()),
  getRiskScores: (runId: string) => fetch(`${BASE}/predict/risk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId }),
  }).then(r => r.json()),
  triggerPipeline: (repoUrl: string, branch: string) => fetch(`${BASE}/pipeline/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_url: repoUrl, branch }),
  }).then(r => r.json()),
};
```

---

## Environment Variables

```bash
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

---

## Running the Frontend

```bash
cd frontend
npm install
npm run dev        # development server on port 3000
npm run build      # production build
npm run start      # start production server
npm run lint       # ESLint
```

---

## Common Issues & Resolutions

**Issue:** CORS errors when the frontend calls the API in development.
**Resolution:** Add the following to `app/main.py`:
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"])
```

**Issue:** `react-diff-viewer-continued` renders blank on SSR.
**Resolution:** Add `"use client"` directive to `FileDiffViewer` (already shown above). The
diff library uses browser-only APIs.

**Issue:** Heatmap cells are all the same size even though `total_churn` varies.
**Resolution:** Use `style={{ width: Math.max(16, Math.min(48, churn / 10)), height: ... }}`
to scale cell size proportionally to churn.

**Issue:** Pipeline status card stops polling before all stages complete.
**Resolution:** The `refetchInterval` function returns `false` when no stage is `RUNNING`.
Ensure the orchestrator marks stages as `RUNNING` at start, not just `COMPLETE` at end.
