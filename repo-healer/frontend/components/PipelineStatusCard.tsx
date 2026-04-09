"use client";

import { useQuery } from "@tanstack/react-query";

type StageStatus = "PENDING" | "RUNNING" | "COMPLETE" | "FAILED" | "SKIPPED";

interface RunContext {
  run_id: string;
  repo_url: string;
  stage_flags: Record<string, StageStatus>;
  pr_url: string | null;
  analysis: Array<Record<string, unknown>>;
  risk: Array<Record<string, unknown>>;
  fixes: Array<Record<string, unknown>>;
  validations: Array<Record<string, unknown>>;
}

const STAGE_ORDER = ["analysis", "complexity", "risk", "healer", "validation", "pr"];

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export function PipelineStatusCard({ runId }: { runId: string }) {
  const { data, isLoading } = useQuery<RunContext>({
    queryKey: ["pipeline", runId],
    queryFn: () =>
      fetch(`${BASE}/pipeline/${runId}`).then((r) => r.json()),
    refetchInterval: (query) => {
      const flags = query.state.data?.stage_flags ?? {};
      const isRunning = Object.values(flags).includes("RUNNING");
      return isRunning ? 3000 : false;
    },
  });

  if (isLoading)
    return <div className="animate-pulse h-32 bg-gray-800/50 rounded-xl" />;

  return (
    <div className="rounded-xl border border-gray-700/50 bg-gray-800/40 backdrop-blur-sm p-6 shadow-lg">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-mono text-gray-400">{runId}</h2>
        {data?.repo_url && (
          <span className="text-xs text-gray-500 truncate max-w-[200px]">
            {data.repo_url}
          </span>
        )}
      </div>
      <div className="flex gap-2 flex-wrap">
        {STAGE_ORDER.map((stage) => (
          <StageBadge
            key={stage}
            stage={stage}
            status={data?.stage_flags[stage] ?? "PENDING"}
          />
        ))}
      </div>

      {/* Summary Stats */}
      <div className="mt-4 grid grid-cols-4 gap-3">
        <StatCard label="Files Analyzed" value={data?.analysis?.length ?? 0} />
        <StatCard
          label="High Risk"
          value={
            data?.risk?.filter((r: Record<string, unknown>) => r.risk_level === "HIGH").length ?? 0
          }
          highlight
        />
        <StatCard label="Fixed" value={data?.fixes?.length ?? 0} />
        <StatCard
          label="Validated"
          value={
            data?.validations?.filter((v: Record<string, unknown>) => v.status === "PASS").length ?? 0
          }
        />
      </div>

      {data?.pr_url && (
        <a
          href={data.pr_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-flex items-center gap-1.5 text-sm text-emerald-400 hover:text-emerald-300 transition-colors"
        >
          <span>🔗</span> View Pull Request →
        </a>
      )}
    </div>
  );
}

function StageBadge({
  stage,
  status,
}: {
  stage: string;
  status: StageStatus;
}) {
  const colours: Record<StageStatus, string> = {
    PENDING: "bg-gray-700/60 text-gray-400 border-gray-600",
    RUNNING: "bg-blue-900/40 text-blue-400 border-blue-500/50 animate-pulse",
    COMPLETE: "bg-emerald-900/40 text-emerald-400 border-emerald-500/50",
    FAILED: "bg-red-900/40 text-red-400 border-red-500/50",
    SKIPPED: "bg-yellow-900/40 text-yellow-400 border-yellow-500/50",
  };
  return (
    <span
      className={`text-xs font-medium px-2.5 py-1 rounded-full capitalize border ${colours[status]}`}
    >
      {stage} - {status.toLowerCase()}
    </span>
  );
}

function StatCard({
  label,
  value,
  highlight = false,
}: {
  label: string;
  value: number;
  highlight?: boolean;
}) {
  return (
    <div className="rounded-lg bg-gray-700/30 p-3 text-center">
      <div
        className={`text-2xl font-bold ${highlight ? "text-red-400" : "text-white"}`}
      >
        {value}
      </div>
      <div className="text-xs text-gray-400 mt-1">{label}</div>
    </div>
  );
}
