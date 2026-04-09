"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface FeatureContribution {
  name: string;
  label: string;
  raw_value: number;
  z_score: number;
  contribution: number;
  severity: string;
}

interface RiskExplanation {
  file: string;
  risk_score: number;
  risk_level: string;
  reasons: string[];
  feature_contributions: FeatureContribution[];
  top_driver: string;
}

interface ExplainabilityReport {
  run_id: string;
  repo_url: string;
  total_files: number;
  high_risk_count: number;
  risk_threshold: number;
  methodology: string;
  explanations: RiskExplanation[];
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-red-500",
  high: "bg-orange-500",
  elevated: "bg-yellow-500",
  normal: "bg-emerald-500",
};

const LEVEL_BADGE: Record<string, string> = {
  HIGH: "bg-red-900/50 text-red-400 border-red-500/40",
  MEDIUM: "bg-yellow-900/50 text-yellow-400 border-yellow-500/40",
  LOW: "bg-emerald-900/50 text-emerald-400 border-emerald-500/40",
};

function FileExplanation({ explanation }: { explanation: RiskExplanation }) {
  const [expanded, setExpanded] = useState(
    explanation.risk_level === "HIGH"
  );

  return (
    <div className="rounded-lg border border-gray-700/50 bg-gray-800/30 overflow-hidden transition-all">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-700/20 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span
            className={`text-xs font-semibold px-2 py-0.5 rounded border ${LEVEL_BADGE[explanation.risk_level] || LEVEL_BADGE.LOW}`}
          >
            {explanation.risk_level}
          </span>
          <span className="text-sm font-mono text-white">
            {explanation.file}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">
            Score: {(explanation.risk_score * 100).toFixed(1)}%
          </span>
          <span className="text-xs text-gray-500">
            Top: {explanation.top_driver}
          </span>
          <span className="text-gray-500 text-sm">
            {expanded ? "▲" : "▼"}
          </span>
        </div>
      </button>

      {/* Expanded Detail */}
      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-gray-700/30">
          {/* Reasons */}
          <div className="mt-3">
            <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
              Why this file was flagged
            </h4>
            <ul className="space-y-1">
              {explanation.reasons.map((reason, i) => (
                <li
                  key={i}
                  className="text-sm text-gray-300 flex items-start gap-2"
                >
                  <span className="text-amber-400 mt-0.5">!</span>
                  {reason}
                </li>
              ))}
            </ul>
          </div>

          {/* Feature Bars */}
          {explanation.feature_contributions.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Feature Contributions
              </h4>
              <div className="space-y-2">
                {explanation.feature_contributions.map((fc) => (
                  <div key={fc.name} className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-gray-300">{fc.label}</span>
                      <span className="text-gray-500">
                        {fc.raw_value} (z={fc.z_score > 0 ? "+" : ""}
                        {fc.z_score})
                      </span>
                    </div>
                    <div className="h-2 bg-gray-700/60 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${SEVERITY_COLORS[fc.severity] || SEVERITY_COLORS.normal}`}
                        style={{
                          width: `${Math.min(fc.contribution * 100, 100)}%`,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function RiskExplainPanel({ runId }: { runId: string }) {
  const [showReport, setShowReport] = useState(false);

  const { data, isLoading, isError, refetch } = useQuery<ExplainabilityReport>(
    {
      queryKey: ["explainability", runId],
      queryFn: () => api.getExplainability(runId),
      enabled: showReport,
    }
  );

  if (!showReport) {
    return (
      <button
        onClick={() => setShowReport(true)}
        className="w-full py-3 rounded-xl border border-amber-500/30 bg-amber-900/10 text-amber-400 font-medium text-sm hover:bg-amber-900/20 hover:border-amber-500/50 transition-all flex items-center justify-center gap-2"
      >
        <span>🔎</span> View Risk Explainability Report
      </button>
    );
  }

  if (isLoading) {
    return (
      <div className="rounded-xl border border-gray-700/50 bg-gray-800/30 p-8 text-center">
        <div className="animate-pulse text-gray-400">
          Generating explainability report...
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="rounded-xl border border-red-700/50 bg-red-900/10 p-4 text-center">
        <p className="text-red-400 text-sm">
          Failed to load report. Make sure the pipeline has completed.
        </p>
        <button
          onClick={() => refetch()}
          className="mt-2 text-xs text-red-300 underline"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-gray-700/50 bg-gray-800/20 backdrop-blur-sm p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <span className="text-amber-400">🔎</span> Risk Explainability Report
        </h2>
        <button
          onClick={() => setShowReport(false)}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          Collapse
        </button>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg bg-gray-700/30 p-3 text-center">
          <div className="text-2xl font-bold text-white">
            {data.total_files}
          </div>
          <div className="text-xs text-gray-400">Files Scanned</div>
        </div>
        <div className="rounded-lg bg-gray-700/30 p-3 text-center">
          <div className="text-2xl font-bold text-red-400">
            {data.high_risk_count}
          </div>
          <div className="text-xs text-gray-400">High Risk</div>
        </div>
        <div className="rounded-lg bg-gray-700/30 p-3 text-center">
          <div className="text-2xl font-bold text-amber-400">
            {(data.risk_threshold * 100).toFixed(0)}%
          </div>
          <div className="text-xs text-gray-400">Risk Threshold</div>
        </div>
      </div>

      {/* Methodology */}
      <div className="rounded-lg bg-blue-900/10 border border-blue-700/30 p-3">
        <h4 className="text-xs font-semibold text-blue-400 mb-1">
          Methodology
        </h4>
        <p className="text-xs text-gray-400 leading-relaxed">
          {data.methodology}
        </p>
      </div>

      {/* File Explanations */}
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-gray-300">
          Per-File Analysis ({data.explanations.length} files)
        </h3>
        {data.explanations.map((exp) => (
          <FileExplanation key={exp.file} explanation={exp} />
        ))}
      </div>
    </div>
  );
}
