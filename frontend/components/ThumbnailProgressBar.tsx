import React from "react";

interface ThumbnailProgressBarProps {
  completed: number;
  total: number;
  currentName: string | null;
  justFinished: boolean;
}

// Fixed-height bar pinned to the bottom of the window (Option A from the
// approved mockup). It either isn't rendered, is showing live progress, or
// is briefly showing the just-finished state -- its own height never
// depends on its content, so it can't reintroduce the card-grid layout
// shift it exists to make visible progress on instead of leaving Dave
// guessing whether generation is still running.
const ThumbnailProgressBar: React.FC<ThumbnailProgressBarProps> = ({
  completed,
  total,
  currentName,
  justFinished,
}) => {
  const remaining = total - completed;
  if (remaining <= 0 && !justFinished) return null;

  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  return (
    <div className="fixed inset-x-0 bottom-0 z-40 h-[34px] bg-vault-800 border-t border-vault-700 flex items-center gap-2.5 px-3.5 text-xs">
      {remaining > 0 ? (
        <>
          <div className="w-3 h-3 rounded-full border-2 border-vault-700 border-t-blue-500 animate-spin shrink-0" />
          <span className="text-slate-400 truncate">
            Generating thumbnails
            {currentName ? (
              <>
                {"… "}
                <span className="text-slate-200">{currentName}</span>
              </>
            ) : (
              "…"
            )}
          </span>
          <div className="flex-1 max-w-[220px] h-1 bg-vault-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-[width] duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-slate-500 tabular-nums whitespace-nowrap">
            {completed.toLocaleString()} / {total.toLocaleString()}
          </span>
        </>
      ) : (
        <>
          <svg
            viewBox="0 0 24 24"
            width="13"
            height="13"
            fill="none"
            stroke="#22c55e"
            strokeWidth="2.5"
          >
            <path d="M20 6 9 17l-5-5" />
          </svg>
          <span className="text-slate-200">
            All {total.toLocaleString()} thumbnails generated
          </span>
        </>
      )}
    </div>
  );
};

export default ThumbnailProgressBar;
