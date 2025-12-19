import React, { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { me, signup } from "@/api/auth";
import Navbar from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function getSafeNext(search) {
  const next = new URLSearchParams(search).get("next") || "/";
  // Only allow a safe relative path.
  if (!next.startsWith("/") || next.startsWith("//") || next.includes("://")) return "/";
  // Avoid redirect loops back to auth pages.
  if (next === "/login" || next.startsWith("/login?")) return "/";
  if (next === "/signup" || next.startsWith("/signup?")) return "/";
  return next;
}

export default function Signup() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const next = useMemo(() => getSafeNext(location.search), [location.search]);

  const { data: user } = useQuery({
    queryKey: ["currentUser"],
    queryFn: () => me(),
    retry: false,
  });

  useEffect(() => {
    if (user) navigate(next);
  }, [user, navigate, next]);

  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");

  const signupMutation = useMutation({
    mutationFn: async () => {
      setError("");

      const dn = displayName.trim();
      if (!dn) throw new Error("Name is required");
      if (password.length < 8) throw new Error("Password must be at least 8 characters");
      if (password !== confirm) throw new Error("Passwords do not match");

      await signup(email, password, dn);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["currentUser"] });
      navigate(next);
    },
    onError: (e) => {
      const msg =
        e?.data?.detail ||
        (typeof e?.message === "string" ? e.message : null) ||
        "Signup failed";
      setError(msg);
    },
  });

  const onSubmit = (e) => {
    e.preventDefault();
    if (!displayName.trim() || !email.trim() || !password || !confirm) return;
    signupMutation.mutate();
  };

  return (
    <div className="min-h-screen relative overflow-hidden">
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 -left-32 w-96 h-96 bg-pink-500/10 rounded-full blur-[120px]" />
        <div className="absolute top-1/3 -right-40 w-[520px] h-[520px] bg-purple-500/10 rounded-full blur-[140px]" />
        <div className="absolute bottom-1/4 left-1/3 w-96 h-96 bg-blue-500/10 rounded-full blur-[120px]" />
      </div>

      <Navbar authVariant="none" />

      <main className="relative z-10 px-6 lg:px-16 pt-16 pb-20">
        <div className="max-w-md mx-auto">
          <Card className="bg-white/5 border-white/10 text-white">
            <CardHeader>
              <CardTitle className="text-2xl font-bold">Create your account</CardTitle>
              <p className="text-sm text-gray-400">
                Start organizing your materials and get an AI assistant that understands your course content.
              </p>
            </CardHeader>
            <CardContent>
              <form onSubmit={onSubmit} className="space-y-5">
                <div className="space-y-2">
                  <Label className="text-gray-300">Name</Label>
                  <Input
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="e.g., John"
                    className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50"
                    autoComplete="name"
                    required
                  />
                </div>

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
                    placeholder="At least 8 characters"
                    className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50"
                    autoComplete="new-password"
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label className="text-gray-300">Confirm password</Label>
                  <Input
                    type="password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    placeholder="Re-enter your password"
                    className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50"
                    autoComplete="new-password"
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
                  disabled={
                    signupMutation.isPending ||
                    !displayName.trim() ||
                    !email.trim() ||
                    !password ||
                    !confirm
                  }
                  className="w-full btn-gradient rounded-xl h-12 font-semibold"
                >
                  {signupMutation.isPending ? "Creating account..." : "Create account"}
                </Button>

                <div className="text-xs text-gray-500">
                  Already have an account?{" "}
                  <Link
                    to={`/login?next=${encodeURIComponent(next)}`}
                    className="text-purple-300 hover:underline"
                  >
                    Sign in
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


