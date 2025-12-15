import './App.css'
import Pages from "@/pages/index.jsx"
import { Toaster } from "@/components/ui/toaster"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, { useEffect } from "react";
import { ensureCsrf } from "@/api/http";

const queryClient = new QueryClient();

function App() {
  useEffect(() => {
    // Bootstrap CSRF cookie early so the first POST (login/refresh/logout) succeeds.
    ensureCsrf().catch(() => {});
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <Pages />
      <Toaster />
    </QueryClientProvider>
  )
}

export default App