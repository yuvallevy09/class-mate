import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import CitationPopover from "./CitationPopover";
import { mergeRanges } from "./citations";

/**
 * Inline citation chip: "Lect 5: Backprop… +1" with a hover/tap popover.
 * `indices` may carry several sources (merged adjacent markers); distinct
 * sources page inside the popover and the chip shows "+N".
 * Hover: 70ms open intent; 220ms leave grace (cleared when entering content).
 * Click/tap toggles. `coordinator` is a shared mutable ref-object per message
 * ensuring only one popover is open at a time.
 */
export default function CitationPill({
  indices = [],
  citationModel,
  coordinator,
  popIn,
  onSeek,
  currentContentId,
}) {
  const [open, setOpen] = useState(false);
  const pillRef = useRef(null);
  const openTimer = useRef(null);
  const closeTimer = useRef(null);

  // One popover model per distinct lecture/source cited at THIS spot. Video
  // models collect only the ranges of this pill's own indices (the moments
  // actually cited here, not the whole lecture's retrieved set), then merge any
  // that overlap or sit within a few seconds of each other into one chip.
  const models = useMemo(() => {
    const byGroup = new Map();
    for (const i of indices) {
      const m = citationModel?.byIndex?.get(i);
      if (!m) continue;
      let entry = byGroup.get(m.groupKey);
      if (!entry) {
        entry = { ...m, ranges: [] };
        delete entry.range;
        byGroup.set(m.groupKey, entry);
      }
      if (m.range) entry.ranges.push(m.range);
    }
    return Array.from(byGroup.values()).map((m) =>
      m.kind === "video" ? { ...m, ranges: mergeRanges(m.ranges) } : m
    );
  }, [indices, citationModel]);

  useEffect(
    () => () => {
      clearTimeout(openTimer.current);
      clearTimeout(closeTimer.current);
    },
    []
  );

  const doOpen = useCallback(() => {
    if (coordinator) {
      if (coordinator.close && coordinator.close !== setOpen) coordinator.close(false);
      coordinator.close = setOpen;
    }
    setOpen(true);
  }, [coordinator]);

  const doClose = useCallback(() => setOpen(false), []);

  const handleEnter = () => {
    clearTimeout(closeTimer.current);
    openTimer.current = setTimeout(doOpen, 70);
  };
  const handleLeave = () => {
    clearTimeout(openTimer.current);
    closeTimer.current = setTimeout(doClose, 220);
  };

  const primary = models[0] || null;
  const label = primary?.chipLabel || primary?.title || `Source ${indices[0] ?? ""}`;
  const extraCount = Math.max(0, models.length - 1);

  return (
    <Popover open={open} onOpenChange={(o) => (o ? doOpen() : doClose())}>
      <PopoverAnchor asChild>
        <button
          ref={pillRef}
          type="button"
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
          onClick={() => (open ? doClose() : doOpen())}
          aria-label={`Source: ${label}${extraCount ? ` and ${extraCount} more` : ""}`}
          className={[
            "mx-0.5 inline-flex h-[17px] items-center justify-center gap-1 rounded-full px-[7px] align-[1px]",
            "whitespace-nowrap font-mono text-[9.5px] font-medium leading-none transition-all duration-150",
            open
              ? "-translate-y-px bg-purple-500/30 text-purple-200 shadow-[inset_0_0_0_1px_rgba(139,92,246,0.55),0_0_14px_rgba(139,92,246,0.25)]"
              : "bg-purple-500/15 text-purple-300 shadow-[inset_0_0_0_1px_rgba(139,92,246,0.28)] hover:-translate-y-px hover:bg-purple-500/30 hover:text-purple-200 hover:shadow-[inset_0_0_0_1px_rgba(139,92,246,0.55),0_0_14px_rgba(139,92,246,0.25)]",
            popIn ? "cm-pop-in" : "",
          ].join(" ")}
        >
          <span className="max-w-[95px] truncate">{label}</span>
          {extraCount > 0 ? <span className="font-normal opacity-60">+{extraCount}</span> : null}
        </button>
      </PopoverAnchor>
      {models.length ? (
        <PopoverContent
          side="bottom"
          sideOffset={8}
          collisionPadding={{ top: 12, left: 12, right: 12, bottom: 108 }}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onMouseEnter={() => clearTimeout(closeTimer.current)}
          onMouseLeave={handleLeave}
          onInteractOutside={(e) => {
            if (pillRef.current?.contains(e.target)) e.preventDefault();
          }}
          className="w-[330px] max-w-[calc(100vw-24px)] rounded-2xl border-white/[0.13] bg-[#16151C]/90 p-4 text-left shadow-[0_16px_40px_rgba(0,0,0,0.55),0_0_32px_rgba(139,92,246,0.07)] backdrop-blur-xl"
        >
          <CitationPopover models={models} onSeek={onSeek} currentContentId={currentContentId} />
        </PopoverContent>
      ) : null}
    </Popover>
  );
}
