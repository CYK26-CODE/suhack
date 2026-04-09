"use client";

interface RiskRecord {
  file: string;
  risk_score: number;
  risk_level: "HIGH" | "MEDIUM" | "LOW";
}

function riskColour(level: string) {
  if (level === "HIGH") return "bg-red-500 hover:bg-red-400";
  if (level === "MEDIUM") return "bg-amber-500 hover:bg-amber-400";
  return "bg-emerald-500 hover:bg-emerald-400";
}

function riskGlow(level: string) {
  if (level === "HIGH") return "shadow-red-500/30";
  if (level === "MEDIUM") return "shadow-amber-500/30";
  return "shadow-emerald-500/30";
}

export function RiskHeatmap({ risks }: { risks: RiskRecord[] }) {
  if (!risks || risks.length === 0) {
    return (
      <div className="text-gray-500 text-center py-12">
        No risk data available. Run a pipeline first.
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-1.5 p-4">
      {risks
        .sort((a, b) => b.risk_score - a.risk_score)
        .map((r) => (
          <a
            key={r.file}
            href={`/file/${encodeURIComponent(r.file)}`}
            title={`${r.file}\nScore: ${r.risk_score.toFixed(3)}\nLevel: ${r.risk_level}`}
            className={`block rounded-md cursor-pointer transition-all duration-200 shadow-md hover:shadow-lg hover:scale-110 ${riskColour(r.risk_level)} ${riskGlow(r.risk_level)}`}
            style={{
              width: Math.max(20, Math.min(48, (r.risk_score * 40) + 16)),
              height: Math.max(20, Math.min(48, (r.risk_score * 40) + 16)),
            }}
          />
        ))}
    </div>
  );
}
