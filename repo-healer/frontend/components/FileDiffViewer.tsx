"use client";

import dynamic from "next/dynamic";

const ReactDiffViewer = dynamic(() => import("react-diff-viewer-continued"), {
  ssr: false,
  loading: () => <div className="animate-pulse h-64 bg-gray-800/50 rounded-xl" />,
});

interface Props {
  original: string;
  fixed: string;
  filename: string;
}

export function FileDiffViewer({ original, fixed, filename }: Props) {
  return (
    <div className="rounded-xl overflow-hidden border border-gray-700/50 shadow-lg">
      <div className="bg-gray-800/60 backdrop-blur-sm px-4 py-2.5 border-b border-gray-700/50 font-mono text-sm text-gray-300 flex items-center gap-2">
        <span className="text-emerald-400">📄</span>
        {filename}
      </div>
      <ReactDiffViewer
        oldValue={original}
        newValue={fixed}
        splitView={true}
        leftTitle="Original"
        rightTitle="Healed"
        useDarkTheme={true}
        hideLineNumbers={false}
      />
    </div>
  );
}
