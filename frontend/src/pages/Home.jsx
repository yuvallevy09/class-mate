import React from "react";
import { Link } from "react-router-dom";
import { createPageUrl } from "@/utils";
import { client } from "@/api/client";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Sparkles, BookOpen, Brain, Zap } from "lucide-react";
import Navbar from "@/components/Navbar";

export default function Home() {
  const { data: user } = useQuery({
    queryKey: ["currentUser"],
    queryFn: () => client.auth.me(),
    retry: false,
  });

  const firstName = user?.email?.split("@")[0] || "there";

  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* Background Elements */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 -left-32 w-96 h-96 bg-pink-500/10 rounded-full blur-[120px]" />
        <div className="absolute top-1/3 -right-32 w-96 h-96 bg-purple-500/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-1/4 left-1/3 w-96 h-96 bg-blue-500/10 rounded-full blur-[120px]" />
      </div>

      <Navbar />

      {/* Hero Section */}
      <main className="relative z-10 px-6 lg:px-16 pt-20 lg:pt-32 pb-20">
        <div className="max-w-5xl mx-auto text-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
          >
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full glass-card mb-8">
              <Sparkles className="w-4 h-4 text-purple-400" />
              <span className="text-sm text-gray-300">AI-Powered Learning Assistant</span>
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.05 }}
            className="mb-4"
          >
            <h2 className="text-2xl md:text-3xl text-gray-400 font-medium">
              Welcome back, <span className="gradient-text font-semibold">{firstName}</span>
            </h2>
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="text-5xl md:text-7xl lg:text-8xl font-bold tracking-tight leading-[1.1] mb-8"
          >
            Your courses,{" "}
            <span className="gradient-text">organized</span>
            <br />
            & intelligent
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.2 }}
            className="text-lg md:text-xl text-gray-400 max-w-2xl mx-auto mb-12 leading-relaxed"
          >
            Upload your course materials and get personalized help from an AI teaching assistant 
            that understands your content.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.3 }}
          >
            <Link to={createPageUrl("Courses")}>
              <button className="btn-gradient px-8 py-4 rounded-full text-lg font-semibold text-white inline-flex items-center gap-3">
                Go to My Courses
                <Zap className="w-5 h-5" />
              </button>
            </Link>
          </motion.div>

          {/* Feature Cards */}
          <motion.div
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.5 }}
            className="grid md:grid-cols-3 gap-6 mt-24"
          >
            {[
              {
                icon: BookOpen,
                title: "Organize Content",
                description: "Upload notes, exams, and resources in one place"
              },
              {
                icon: Brain,
                title: "AI Assistant",
                description: "Get answers based on your actual course materials"
              },
              {
                icon: Sparkles,
                title: "Personalized",
                description: "Learning tailored to your courses and style"
              }
            ].map((feature, _index) => (
              <motion.div
                key={feature.title}
                whileHover={{ y: -5, scale: 1.02 }}
                className="glass-card rounded-2xl p-8 text-left group cursor-pointer"
              >
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center mb-5 group-hover:neon-glow transition-all duration-300">
                  <feature.icon className="w-6 h-6 text-purple-400" />
                </div>
                <h3 className="text-xl font-semibold mb-3">{feature.title}</h3>
                <p className="text-gray-400">{feature.description}</p>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </main>
    </div>
  );
}