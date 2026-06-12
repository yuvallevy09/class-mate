import React from "react";
import { Play, FileText, ExternalLink } from "lucide-react";
import { fmtTimestamp, isRedundantChapterTitle } from "./citations";

const chipClass =
  "inline-flex items-center gap-1.5 rounded-lg border border-purple-500/30 bg-purple-500/10 px-2.5 py-1 font-mono text-[11px] tabular-nums text-purple-300 no-underline transition-all hover:-translate-y-px hover:border-purple-500/50 hover:bg-purple-500/25 hover:text-purple-200 hover:shadow-[0_4px_14px_rgba(139,92,246,0.22)]";

function Chip({ href, children }) {
  if (!href) {
    return <span className={chipClass}>{children}</span>;
  }
  return (
    <a href={href} target="_blank" rel="noreferrer" className={chipClass}>
      {children}
    </a>
  );
}

export default function CitationPopover({ model }) {
  if (!model) return null;
  const isVideo = model.kind === "video";
  const showChapter = isVideo && !isRedundantChapterTitle(model.chapterTitle);

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2 font-mono text-[9.5px] font-medium uppercase tracking-[0.16em] text-gray-400">
        {isVideo ? (
          <Play className="h-3 w-3 shrink-0 fill-pink-500 text-pink-500" />
        ) : (
          <FileText className="h-3 w-3 shrink-0 text-blue-300" />
        )}
        <span>{isVideo ? "Video" : "Source"}</span>
        {showChapter && (
          <>
            <span className="opacity-50">·</span>
            <span className="truncate">{model.chapterTitle}</span>
          </>
        )}
      </div>

      <div className="mb-1.5 text-sm font-semibold leading-snug text-white">
        {model.title}
        {!isVideo && model.pageLabel ? (
          <span className="font-normal text-gray-300">{model.pageLabel}</span>
        ) : null}
      </div>

      {model.snippet ? (
        <div className="mb-3 text-xs leading-relaxed text-gray-400 line-clamp-3">
          “{model.snippet}”
        </div>
      ) : null}

      <div className="flex flex-wrap gap-1.5">
        {isVideo ? (
          (model.ranges || []).map((r) => (
            <Chip key={`${r.s}-${r.e}`} href={r.url}>
              <Play className="h-2.5 w-2.5 fill-current" />
              {r.e !== r.s ? `${fmtTimestamp(r.s)}–${fmtTimestamp(r.e)}` : fmtTimestamp(r.s)}
            </Chip>
          ))
        ) : model.url ? (
          <Chip href={model.url}>
            <ExternalLink className="h-2.5 w-2.5" />
            Open source
          </Chip>
        ) : null}
      </div>
    </div>
  );
}
