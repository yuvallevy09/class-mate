import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  ChevronRight,
  BookOpen,
  Image,
  PenTool,
  FileQuestion,
  ClipboardList,
  FolderOpen,
  FileText,
} from "lucide-react";

import { createPageUrl } from "@/utils";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

export const COURSE_SIDEBAR_ITEMS = [
  { id: "overview", label: "Overview", icon: BookOpen },
  { id: "media", label: "Course Media", icon: Image },
  { id: "notes", label: "Notes", icon: PenTool },
  { id: "past_exams", label: "Past Exams", icon: FileQuestion },
  { id: "past_assignments", label: "Past Assignments", icon: ClipboardList },
  { id: "additional_resources", label: "Additional Resources", icon: FolderOpen },
  { id: "general", label: "General", icon: FileText },
];

export default function CourseSidebar({
  courseId,
  isOpen,
  onClose,
  activeCategory,
} = {}) {
  const navigate = useNavigate();

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/50 z-30"
            onClick={() => onClose?.()}
          />
          <motion.aside
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="fixed right-0 top-[73px] bottom-0 w-80 glass-card border-l border-white/5 z-40"
          >
            <div className="p-6 border-b border-white/5 flex items-center justify-between">
              <Link to={createPageUrl("Courses")} onClick={() => onClose?.()}>
                <Button
                  variant="ghost"
                  className="text-gray-400 hover:text-white hover:bg-white/5 px-3"
                >
                  <ChevronRight className="w-4 h-4 mr-2 rotate-180" />
                  My Courses
                </Button>
              </Link>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => onClose?.()}
                className="text-gray-400 hover:text-white hover:bg-white/5"
              >
                <X className="w-5 h-5" />
              </Button>
            </div>

            <ScrollArea className="h-[calc(100%-73px)]">
              <div className="p-4 space-y-6">
                {/* Course Content */}
                <div>
                  <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 px-2">
                    Course Content
                  </h3>
                  <div className="space-y-2">
                    {COURSE_SIDEBAR_ITEMS.map((item) => (
                      <Link
                        key={item.id}
                        to={createPageUrl(
                          `CourseContent?courseId=${courseId}&category=${item.id}`
                        )}
                        onClick={() => onClose?.()}
                      >
                        <motion.button
                          whileHover={{ x: 4 }}
                          className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left transition-colors ${
                            activeCategory === item.id
                              ? "bg-purple-500/20 text-white border border-purple-500/30"
                              : "text-gray-300 hover:text-white hover:bg-white/5"
                          }`}
                        >
                          <item.icon className="w-5 h-5 text-purple-400" />
                          <span className="text-sm font-medium">{item.label}</span>
                        </motion.button>
                      </Link>
                    ))}
                  </div>
                </div>
              </div>
            </ScrollArea>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}


