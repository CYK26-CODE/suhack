"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { PipelineStatusCard } from "@/components/PipelineStatusCard";
import { RiskExplainPanel } from "@/components/RiskExplainCard";
import { api } from "@/lib/api";

export default function DashboardPage() {
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  const triggerMutation = useMutation({
    mutationFn: () => api.triggerPipeline(repoUrl, branch),
    onSuccess: (data) => {
      setActiveRunId(data.run_id);
    },
  });

  return (
    <div className="space-y-8">
      {/* Hero */}
      <div className="text-center py-8">
        <h1 className="text-4xl font-bold bg-gradient-to-r from-emerald-400 via-cyan-400 to-blue-400 bg-clip-text text-transparent mb-3">
          Repository Code Health
        </h1>
        <p className="text-gray-400 text-lg max-w-2xl mx-auto">
          Analyze, predict risk, auto-heal, and submit fixes - all powered by AI
          and static analysis.
        </p>
      </div>

      {/* Pipeline Trigger */}
      <div className="rounded-xl border border-gray-700/50 bg-gray-800/30 backdrop-blur-sm p-6 shadow-lg">
        <h2 className="text-lg font-semibold text-white mb-4">
          🚀 Start Pipeline Run
        </h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="https://github.com/org/repo"
            className="flex-1 rounded-lg bg-gray-900/60 border border-gray-700/50 px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500/50 transition-all"
          />
          <input
            type="text"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="main"
            className="w-32 rounded-lg bg-gray-900/60 border border-gray-700/50 px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 transition-all"
          />
          <button
            onClick={() => triggerMutation.mutate()}
            disabled={!repoUrl || triggerMutation.isPending}
            className="px-6 py-2.5 rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 text-white font-medium text-sm hover:from-emerald-400 hover:to-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-lg shadow-emerald-500/20 hover:shadow-emerald-500/30"
          >
            {triggerMutation.isPending ? (
              <span className="flex items-center gap-2">
                <span className="animate-spin">⏳</span> Running...
              </span>
            ) : (
              "Run Pipeline"
            )}
          </button>
        </div>
        {triggerMutation.isError && (
          <p className="mt-3 text-sm text-red-400">
            Error: {(triggerMutation.error as Error).message}
          </p>
        )}
      </div>

      {/* Active Run Status */}
      {activeRunId && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <span className="text-emerald-400">📊</span> Pipeline Status
          </h2>
          <PipelineStatusCard runId={activeRunId} />
          <RiskExplainPanel runId={activeRunId} />
        </div>
      )}

      {/* Quick Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="rounded-xl border border-gray-700/50 bg-gradient-to-br from-emerald-900/20 to-gray-800/30 p-6">
          <div className="text-3xl mb-2">🔍</div>
          <h3 className="font-semibold text-white mb-1">Static Analysis</h3>
          <p className="text-sm text-gray-400">
            PyDriller + Radon mine commit history and compute cyclomatic complexity.
          </p>
        </div>
        <div className="rounded-xl border border-gray-700/50 bg-gradient-to-br from-amber-900/20 to-gray-800/30 p-6">
          <div className="text-3xl mb-2">🎯</div>
          <h3 className="font-semibold text-white mb-1">Risk Prediction</h3>
          <p className="text-sm text-gray-400">
            IsolationForest ML model identifies files that deviate from the norm.
          </p>
        </div>
        <div className="rounded-xl border border-gray-700/50 bg-gradient-to-br from-blue-900/20 to-gray-800/30 p-6">
          <div className="text-3xl mb-2">✅</div>
          <h3 className="font-semibold text-white mb-1">Validated Fixes</h3>
          <p className="text-sm text-gray-400">
            Every fix passes syntax, flake8, pytest, and complexity regression checks.
          </p>
        </div>
      </div>
    </div>
  );
}
