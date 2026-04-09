"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RiskHeatmap } from "@/components/RiskHeatmap";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export default function HeatmapPage() {
  const [runId, setRunId] = useState("");
  const [queryRunId, setQueryRunId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["risk", queryRunId],
    queryFn: () =>
      fetch(`${BASE}/pipeline/${queryRunId}`).then((r) => r.json()),
    enabled: !!queryRunId,
  });

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold bg-gradient-to-r from-red-400 via-amber-400 to-emerald-400 bg-clip-text text-transparent mb-2">
          Risk Heatmap
        </h1>
        <p className="text-gray-400">
          Colour-coded view of file risk levels. Red = HIGH, Amber = MEDIUM,
          Green = LOW.
        </p>
      </div>

      <div className="flex gap-3">
        <input
          type="text"
          value={runId}
          onChange={(e) => setRunId(e.target.value)}
          placeholder="Enter run ID..."
          className="flex-1 rounded-lg bg-gray-900/60 border border-gray-700/50 px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-amber-500/50 transition-all"
        />
        <button
          onClick={() => setQueryRunId(runId)}
          disabled={!runId}
          className="px-6 py-2.5 rounded-lg bg-gradient-to-r from-amber-500 to-red-500 text-white font-medium text-sm hover:from-amber-400 hover:to-red-400 disabled:opacity-40 transition-all shadow-lg shadow-amber-500/20"
        >
          Load Heatmap
        </button>
      </div>

      {isLoading && (
        <div className="animate-pulse h-48 bg-gray-800/50 rounded-xl" />
      )}

      {error && (
        <div className="text-red-400 text-sm">
          Error: {(error as Error).message}
        </div>
      )}

      {data?.risk && (
        <div className="rounded-xl border border-gray-700/50 bg-gray-800/30 backdrop-blur-sm p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">
              {data.risk.length} files analyzed
            </h2>
            <div className="flex gap-4 text-xs text-gray-400">
              <span className="flex items-center gap-1.5">
                <span className="w-3 h-3 rounded-sm bg-red-500" /> HIGH
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-3 h-3 rounded-sm bg-amber-500" /> MEDIUM
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-3 h-3 rounded-sm bg-emerald-500" /> LOW
              </span>
            </div>
          </div>
          <RiskHeatmap risks={data.risk} />
        </div>
      )}
    </div>
  );
}
