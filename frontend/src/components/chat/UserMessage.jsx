import React from "react";

/** Quiet neutral user bubble (no gradient). */
export default function UserMessage({ content }) {
  return (
    <div className="max-w-[75%] rounded-2xl border border-white/[0.08] bg-white/[0.06] px-[18px] py-[11px]">
      <p className="text-sm lg:text-base leading-relaxed whitespace-pre-wrap text-gray-100">
        {content}
      </p>
    </div>
  );
}
