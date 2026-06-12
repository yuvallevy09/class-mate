import React, { useCallback, useEffect, useRef, useState } from "react";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import CitationPopover from "./CitationPopover";

/**
 * Inline citation pill with a hover/tap popover.
 * Hover: 70ms open intent; 220ms leave grace (cleared when entering content).
 * Click/tap toggles. `coordinator` is a shared mutable ref-object per message
 * ensuring only one popover is open at a time.
 */
export default function CitationPill({ index, model, coordinator, popIn }) {
  const [open, setOpen] = useState(false);
  const pillRef = useRef(null);
  const openTimer = useRef(null);
  const closeTimer = useRef(null);

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

  const displayNumber = model?.displayNumber ?? index;

  return (
    <Popover open={open} onOpenChange={(o) => (o ? doOpen() : doClose())}>
      <PopoverAnchor asChild>
        <button
          ref={pillRef}
          type="button"
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
          onClick={() => (open ? doClose() : doOpen())}
          aria-label={`Source ${displayNumber}${model?.title ? `: ${model.title}` : ""}`}
          className={[
            "mx-px inline-flex h-4 min-w-[17px] items-center justify-center rounded-full px-[5px] align-[1px]",
            "font-mono text-[10px] font-medium leading-none transition-all duration-150",
            open
              ? "-translate-y-px bg-purple-500/30 text-purple-200 shadow-[inset_0_0_0_1px_rgba(139,92,246,0.55),0_0_14px_rgba(139,92,246,0.25)]"
              : "bg-purple-500/15 text-purple-300 shadow-[inset_0_0_0_1px_rgba(139,92,246,0.28)] hover:-translate-y-px hover:bg-purple-500/30 hover:text-purple-200 hover:shadow-[inset_0_0_0_1px_rgba(139,92,246,0.55),0_0_14px_rgba(139,92,246,0.25)]",
            popIn ? "cm-pop-in" : "",
          ].join(" ")}
        >
          {displayNumber}
        </button>
      </PopoverAnchor>
      {model ? (
        <PopoverContent
          side="top"
          sideOffset={8}
          collisionPadding={12}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onMouseEnter={() => clearTimeout(closeTimer.current)}
          onMouseLeave={handleLeave}
          onInteractOutside={(e) => {
            if (pillRef.current?.contains(e.target)) e.preventDefault();
          }}
          className="w-[330px] max-w-[calc(100vw-24px)] rounded-2xl border-white/[0.13] bg-[#16151C]/90 p-4 text-left shadow-[0_16px_40px_rgba(0,0,0,0.55),0_0_32px_rgba(139,92,246,0.07)] backdrop-blur-xl"
        >
          <CitationPopover model={model} />
        </PopoverContent>
      ) : null}
    </Popover>
  );
}
