import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FEEDBACK_ENABLED } from "@/lib/featureFlags";
import { getFeedbackOptIn, setFeedbackOptIn } from "@/api/feedback";

/**
 * Global "Help ClassMate learn" opt-in. Backed by the stub API (localStorage)
 * today; when the backend lands this becomes part of the currentUser payload.
 * The React Query cache keeps it reactive across the Navbar toggle and the
 * per-answer rating rows without a shared context.
 */
export function useFeedbackOptIn() {
  const qc = useQueryClient();

  const { data: optedIn = false } = useQuery({
    queryKey: ["feedbackOptIn"],
    queryFn: getFeedbackOptIn,
    enabled: FEEDBACK_ENABLED,
    staleTime: Infinity,
  });

  const setOptedIn = async (value) => {
    qc.setQueryData(["feedbackOptIn"], value); // optimistic
    try {
      await setFeedbackOptIn(value);
    } catch {
      qc.setQueryData(["feedbackOptIn"], !value); // revert on failure
    } finally {
      qc.invalidateQueries({ queryKey: ["feedbackOptIn"] });
    }
  };

  // Fold the flag in here so callers never need to check it separately.
  return { optedIn: FEEDBACK_ENABLED && optedIn, setOptedIn };
}
