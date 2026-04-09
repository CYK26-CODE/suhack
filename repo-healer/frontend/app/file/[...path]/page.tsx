"use client";

import { useParams } from "next/navigation";
import { FileDiffViewer } from "@/components/FileDiffViewer";

export default function FileViewerPage() {
  const params = useParams();
  const filePath = Array.isArray(params.path)
    ? params.path.join("/")
    : params.path || "";

  // In a real implementation, this would fetch from the API
  // For now, show a placeholder UI
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white mb-1">File Viewer</h1>
        <p className="text-gray-400 font-mono text-sm">{filePath}</p>
      </div>

      {/* Risk Metadata Header */}
      <div className="rounded-xl border border-gray-700/50 bg-gray-800/30 backdrop-blur-sm p-5 flex items-center gap-6">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-red-900/40 text-red-400 border border-red-500/50">
            HIGH RISK
          </span>
        </div>
        <div className="text-sm text-gray-400">
          <span className="text-gray-500">Complexity:</span>{" "}
          <span className="text-white font-mono">—</span>
        </div>
        <div className="text-sm text-gray-400">
          <span className="text-gray-500">Contributors:</span>{" "}
          <span className="text-white font-mono">—</span>
        </div>
        <div className="text-sm text-gray-400">
          <span className="text-gray-500">Last Modified:</span>{" "}
          <span className="text-white font-mono">—</span>
        </div>
      </div>

      {/* Diff Viewer */}
      <FileDiffViewer
        original={"# Original source code will appear here\n# when connected to a live pipeline run\n"}
        fixed={"# Healed source code will appear here\n# after the LLM healing stage\n"}
        filename={filePath}
      />

      {/* Validation Results */}
      <div className="rounded-xl border border-gray-700/50 bg-gray-800/30 backdrop-blur-sm p-5">
        <h2 className="text-lg font-semibold text-white mb-3">
          Validation Results
        </h2>
        <div className="grid grid-cols-4 gap-3">
          {["syntax", "flake8", "pytest", "complexity"].map((check) => (
            <div
              key={check}
              className="rounded-lg bg-gray-700/30 p-3 text-center"
            >
              <div className="text-lg mb-1">⏳</div>
              <div className="text-xs text-gray-400 capitalize">{check}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
