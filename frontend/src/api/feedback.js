// STUB feedback API — persists to localStorage so the "Help ClassMate learn"
// UI is fully clickable without a backend. Each function documents the real
// endpoint to swap in once the backend lands:
//
//   PATCH /api/v1/users/me                     { feedback_opt_in }
//   PATCH /api/v1/messages/{id}/feedback       { rating, comment, category }
//   GET   /api/v1/conversations/{id}/messages  -> each assistant msg gains `feedback`
//
// When the backend exists, the opt-in comes from the currentUser payload and
// each message's saved rating comes from `msg.feedback`, so `getStoredFeedback`
// below becomes unused.

const OPT_IN_KEY = "classmate.feedback.optIn";
const fbKey = (messageId) => `classmate.feedback.msg.${messageId}`;

function safeGet(key) {
  try {
    return typeof localStorage !== "undefined" ? localStorage.getItem(key) : null;
  } catch {
    return null;
  }
}
function safeSet(key, val) {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(key, val);
  } catch {
    /* ignore quota / private-mode errors */
  }
}

// Simulate a little network latency so optimistic UI paths get exercised.
const tick = () => new Promise((r) => setTimeout(r, 150));

export async function getFeedbackOptIn() {
  await tick();
  return safeGet(OPT_IN_KEY) === "true";
  // Real: return (await me())?.feedback_opt_in ?? false;
}

export async function setFeedbackOptIn(value) {
  await tick();
  safeSet(OPT_IN_KEY, value ? "true" : "false");
  return { feedback_opt_in: !!value };
  // Real: return request("/api/v1/users/me", { method: "PATCH", body: { feedback_opt_in: !!value } });
}

// Stub-only: seed a message's saved rating from localStorage. With a backend
// this data arrives on the message payload as `msg.feedback` instead.
export function getStoredFeedback(messageId) {
  if (!messageId) return null;
  const raw = safeGet(fbKey(messageId));
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export async function submitMessageFeedback(messageId, { rating, comment, category }) {
  if (!messageId) throw new Error("messageId is required");
  await tick();
  const payload = {
    rating,
    comment: comment ? String(comment).trim() || null : null,
    // Stage category — required by the backend when rating <= 2 (null otherwise).
    category: category || null,
  };
  safeSet(fbKey(messageId), JSON.stringify(payload));
  return payload;
  // Real: return request(`/api/v1/messages/${encodeURIComponent(messageId)}/feedback`,
  //         { method: "PATCH", body: payload });
}
