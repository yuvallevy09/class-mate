import { Navigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { me } from "@/api/auth";
import { Button } from "@/components/ui/button";

export default function RequireAuth({ children }) {
  const location = useLocation();

  const {
    data: user,
    status,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ["currentUser"],
    queryFn: () => me(),
    retry: false,
  });

  if (status === "pending") return null;

  if (status === "error") {
    const message =
      (typeof error?.message === "string" && error.message) ||
      "Failed to load session. Please check the API/CORS and try again.";

    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="max-w-md w-full glass-card rounded-2xl p-6">
          <div className="text-lg font-semibold mb-2">Can’t reach the server</div>
          <div className="text-sm text-gray-400 mb-4">{message}</div>
          <Button onClick={() => refetch()} disabled={isFetching} className="btn-gradient rounded-xl">
            {isFetching ? "Retrying..." : "Retry"}
          </Button>
        </div>
      </div>
    );
  }

  // status === "success"
  if (!user) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  return children;
}


