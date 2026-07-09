// Centralized frontend feature flags, read from Vite env at build time.
// Flip these in frontend/.env — e.g. `VITE_FEEDBACK_ENABLED=true` — then restart
// the dev server (Vite only reads env on startup).

/**
 * Parse a truthy env flag. Treats missing/empty as the provided default;
 * only the literal string "true" (case-insensitive) enables it. Mirrors the
 * existing VITE_CHAT_ENABLED convention used in the app.
 */
function readBoolFlag(value, defaultValue = false) {
  if (value === undefined || value === null || String(value).trim() === "") {
    return defaultValue;
  }
  return String(value).trim().toLowerCase() === "true";
}

// "Help ClassMate learn" — per-answer star ratings + feedback capture.
// OFF by default. Requires the backend FEEDBACK_ENABLED flag to be on as well.
export const FEEDBACK_ENABLED = readBoolFlag(import.meta.env.VITE_FEEDBACK_ENABLED, false);
