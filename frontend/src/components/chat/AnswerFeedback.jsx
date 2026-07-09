import React, { useState } from "react";
import { Star, Check } from "lucide-react";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/use-toast";
import { submitMessageFeedback, getStoredFeedback } from "@/api/feedback";

const GOLD = "#D8A64A";
const starStr = (n) => "★".repeat(n) + "☆".repeat(5 - n);

// Ratings at/below this require a category — matches the backend validator
// (MessageFeedbackIn rejects a ≤2★ with no category → 422).
const LOW_MAX = 2;

// Mirrors the backend FeedbackCategory enum; each maps to a pipeline stage the
// optimizer can fix (clarify→router, lecture→retrieval, answer→answer step).
const CATEGORIES = [
  { id: "unnecessary_clarification", label: "Asked me to clarify unnecessarily" },
  { id: "wrong_lecture", label: "Wrong / missing lecture" },
  { id: "bad_answer", label: "Answer wrong, vague, or too long" },
  { id: "other", label: "Something else" },
];
const catLabel = (id) => CATEGORIES.find((c) => c.id === id)?.label ?? "";

/**
 * Per-answer rating row under a finished, persisted assistant message.
 * 1–5 stars → popover. A LOW rating (≤2★) requires a "what went wrong?" category
 * (the human credit-assignment signal); higher ratings are just a number + an
 * optional comment. Then a compact "Thanks — noted" confirmation with edit.
 *
 * `initialFeedback` (from the server's msg.feedback) wins when present; in the
 * current stub build it's undefined, so we seed from localStorage instead.
 */
export default function AnswerFeedback({ messageId, initialFeedback }) {
  const [seed] = useState(() => initialFeedback ?? getStoredFeedback(messageId) ?? null);
  const [rating, setRating] = useState(seed?.rating ?? 0);
  const [comment, setComment] = useState(seed?.comment ?? "");
  const [category, setCategory] = useState(seed?.category ?? null);
  const [submitted, setSubmitted] = useState(!!seed?.rating);
  const [hover, setHover] = useState(0);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const shown = hover || rating;
  const low = rating > 0 && rating <= LOW_MAX;
  const canSend = rating > 0 && (!low || !!category);

  const pickStar = (v) => {
    setRating(v);
    setCategory(null); // re-rating clears any prior category
    setOpen(true);
  };

  const commit = async () => {
    if (!canSend) return;
    setSaving(true);
    try {
      await submitMessageFeedback(messageId, {
        rating,
        comment,
        category: low ? category : null,
      });
      setSubmitted(true);
      setOpen(false);
    } catch {
      toast({ title: "Couldn’t save your rating", description: "Please try again." });
    } finally {
      setSaving(false);
    }
  };

  // Collapsed confirmation state.
  if (submitted && !open) {
    return (
      <div className="mt-3 flex items-center gap-2.5 text-xs text-emerald-400/90">
        <Check className="h-3.5 w-3.5" />
        <span>
          Thanks — noted{" "}
          <span style={{ color: GOLD, letterSpacing: "1px" }}>{starStr(rating)}</span>
          {low && category ? <span className="text-gray-400"> · {catLabel(category)}</span> : null}
        </span>
        <button
          onClick={() => {
            setSubmitted(false);
            setOpen(true);
          }}
          className="text-gray-400 underline underline-offset-2 hover:text-purple-300"
        >
          edit
        </button>
      </div>
    );
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <div className="mt-3 flex items-center gap-3 border-t border-white/5 pt-3">
        <span className="text-xs text-gray-400">Rate this answer</span>
        <PopoverAnchor asChild>
          <div className="flex items-center gap-1" onMouseLeave={() => setHover(0)}>
            {[1, 2, 3, 4, 5].map((v) => {
              const lit = v <= shown;
              return (
                <button
                  key={v}
                  type="button"
                  aria-label={`${v} star${v > 1 ? "s" : ""}`}
                  onMouseEnter={() => setHover(v)}
                  onClick={() => pickStar(v)}
                  className="p-0.5 transition-colors"
                  style={{ color: lit ? GOLD : "rgba(255,255,255,0.28)" }}
                >
                  <Star className="h-4 w-4" fill={lit ? GOLD : "none"} strokeWidth={1.6} />
                </button>
              );
            })}
          </div>
        </PopoverAnchor>
      </div>

      <PopoverContent
        align="start"
        sideOffset={8}
        className="w-80 border-white/10 bg-[#16151C]/95 text-white backdrop-blur-xl"
      >
        <div className="mb-2.5 text-sm font-semibold">
          {low ? "What went wrong?" : "Add a comment"}
        </div>

        {low ? (
          <div className="mb-3 flex flex-col gap-1.5">
            {CATEGORIES.map((c) => {
              const sel = category === c.id;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => setCategory(c.id)}
                  className={
                    "w-full rounded-lg border px-3 py-2 text-left text-[13px] transition-colors " +
                    (sel
                      ? "border-purple-400/55 bg-purple-500/15 text-purple-200"
                      : "border-white/10 bg-white/5 text-gray-300 hover:border-white/25 hover:text-white")
                  }
                >
                  {c.label}
                </button>
              );
            })}
          </div>
        ) : null}

        <Textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={low ? "Add more detail (optional)" : "Add a comment (optional)"}
          className="min-h-[64px] resize-y border-white/10 bg-black/25 text-sm focus-visible:ring-purple-400/40"
          autoFocus={!low}
        />
        <div className="mt-3 flex justify-end gap-2">
          {/* No "Skip" on a low rating — a category is required. */}
          {!low ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={commit}
              disabled={saving}
              className="text-gray-300 hover:bg-white/5 hover:text-white"
            >
              Skip
            </Button>
          ) : null}
          <Button
            size="sm"
            onClick={commit}
            disabled={saving || !canSend}
            className="btn-gradient"
          >
            {saving ? "Saving…" : "Send"}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
