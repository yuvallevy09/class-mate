import React, { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import Navbar from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function getSafeNext(search) {
  const next = new URLSearchParams(search).get("next") || "/";
  // Only allow a safe relative path.
  if (!next.startsWith("/") || next.startsWith("//") || next.includes("://")) return "/";
  return next;
}

export default function Login() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const next = useMemo(() => getSafeNext(location.search), [location.search]);

  const { data: user } = useQuery({
    queryKey: ["currentUser"],
    queryFn: () => client.auth.me(),
    retry: false,
  });

  useEffect(() => {
    if (user) navigate(next);
  }, [user, navigate, next]);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const loginMutation = useMutation({
    mutationFn: async () => {
      setError("");
      await client.auth.login({ email, password });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["currentUser"] });
      navigate(next);
    },
    onError: (e) => {
      const msg =
        e?.data?.detail ||
        (typeof e?.message === "string" ? e.message : null) ||
        "Login failed";
      setError(msg);
    },
  });

  const onSubmit = (e) => {
    e.preventDefault();
    if (!email.trim() || !password) return;
    loginMutation.mutate();
  };

  return (
    <div className="min-h-screen relative overflow-hidden">
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 -left-32 w-96 h-96 bg-pink-500/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-1/4 -right-32 w-96 h-96 bg-purple-500/10 rounded-full blur-[120px]" />
      </div>

      <Navbar />

      <main className="relative z-10 px-6 lg:px-16 pt-16 pb-20">
        <div className="max-w-md mx-auto">
          <Card className="bg-white/5 border-white/10 text-white">
            <CardHeader>
              <CardTitle className="text-2xl font-bold">Login</CardTitle>
              <p className="text-sm text-gray-400">
                Sign in to connect the UI to the real backend session.
              </p>
            </CardHeader>
            <CardContent>
              <form onSubmit={onSubmit} className="space-y-5">
                <div className="space-y-2">
                  <Label className="text-gray-300">Email</Label>
                  <Input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50"
                    autoComplete="email"
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label className="text-gray-300">Password</Label>
                  <Input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50"
                    autoComplete="current-password"
                    required
                  />
                </div>

                {error && (
                  <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                    {error}
                  </div>
                )}

                <Button
                  type="submit"
                  disabled={loginMutation.isPending || !email.trim() || !password}
                  className="w-full btn-gradient rounded-xl h-12 font-semibold"
                >
                  {loginMutation.isPending ? "Signing in..." : "Sign in"}
                </Button>

                <div className="text-xs text-gray-500">
                  Need an account? For now, create a dev user via the backend seed script.
                  {" "}
                  <Link to="/" className="text-purple-300 hover:underline">
                    Go home
                  </Link>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}


