import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Minimal controlled switch — avoids adding @radix-ui/react-switch.
 * Usage: <Switch checked={on} onCheckedChange={setOn} aria-label="…" />
 * Pass `pointer-events-none` + aria-hidden to use it purely as a visual
 * indicator when an outer element already owns the interaction.
 */
const Switch = React.forwardRef(function Switch(
  { checked = false, onCheckedChange, disabled = false, className, ...props },
  ref
) {
  return (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onCheckedChange?.(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors outline-none focus-visible:ring-2 focus-visible:ring-purple-400/50 disabled:cursor-not-allowed disabled:opacity-50",
        checked
          ? "border-transparent bg-gradient-to-br from-purple-500 to-blue-500"
          : "border-white/10 bg-white/15",
        className
      )}
      {...props}
    >
      <span
        className={cn(
          "pointer-events-none inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-[18px]" : "translate-x-0.5"
        )}
      />
    </button>
  );
});

export { Switch };
