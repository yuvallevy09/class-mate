import React, { useState } from "react";
import { Link } from "react-router-dom";
import { createPageUrl } from "@/utils";
import { listCourses, createCourse, deleteCourse } from "@/api/courses";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Search, Plus, BookOpen, ChevronRight, Grid3x3, List, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import Navbar from "@/components/Navbar";

const GRADIENT_COLORS = [
  "from-pink-500 to-purple-600",
  "from-purple-500 to-blue-600",
  "from-blue-500 to-cyan-500",
  "from-cyan-500 to-teal-500",
  "from-orange-500 to-pink-500",
  "from-violet-500 to-purple-600"
];

function sortCoursesNewestFirst(items) {
  const arr = Array.isArray(items) ? [...items] : [];
  // Backend returns ISO timestamps in created_at; keep stable fallback.
  arr.sort((a, b) => String(b?.created_at || "").localeCompare(String(a?.created_at || "")));
  return arr;
}

export default function Courses() {
  const [searchQuery, setSearchQuery] = useState("");
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [viewMode, setViewMode] = useState("grid");
  const [newCourse, setNewCourse] = useState({ name: "", description: "" });
  const [courseToDelete, setCourseToDelete] = useState(null);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  
  const queryClient = useQueryClient();

  const { data: courses = [], isLoading } = useQuery({
    queryKey: ['courses'],
    queryFn: async () => sortCoursesNewestFirst(await listCourses())
  });

  const createCourseMutation = useMutation({
    mutationFn: (courseData) =>
      createCourse({
        name: courseData?.name ?? "",
        description: courseData?.description ?? "",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['courses'] });
      setIsAddDialogOpen(false);
      setNewCourse({ name: "", description: "" });
    }
  });

  const deleteCourseMutation = useMutation({
    mutationFn: (courseId) => deleteCourse(courseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["courses"] });
      setIsDeleteDialogOpen(false);
      setCourseToDelete(null);
    },
  });

  const hashString = (s) => {
    let h = 0;
    for (let i = 0; i < s.length; i += 1) {
      h = ((h << 5) - h) + s.charCodeAt(i);
      h |= 0; // keep 32-bit
    }
    return Math.abs(h);
  };

  const coursesWithColor = courses.map((course) => ({
    ...course,
    color:
      course.color ||
      GRADIENT_COLORS[hashString(String(course.id || "")) % GRADIENT_COLORS.length],
  }));

  const filteredCourses = coursesWithColor.filter(course =>
    course.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
    course.description?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleCreateCourse = () => {
    if (newCourse.name.trim()) {
      createCourseMutation.mutate(newCourse);
    }
  };

  const requestDeleteCourse = (e, course) => {
    e?.preventDefault?.();
    e?.stopPropagation?.();
    setCourseToDelete(course);
    setIsDeleteDialogOpen(true);
  };

  return (
    <div className="min-h-screen relative">
      {/* Background */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-0 right-1/4 w-[500px] h-[500px] bg-purple-500/5 rounded-full blur-[150px]" />
        <div className="absolute bottom-0 left-1/4 w-[500px] h-[500px] bg-pink-500/5 rounded-full blur-[150px]" />
      </div>

      <Navbar />

      {/* Main Content */}
      <main className="relative z-10 px-6 lg:px-16 py-8">
        <div className="max-w-7xl mx-auto">
          {/* Page Header */}
          <div className="flex items-start justify-between mb-8">
            <div>
              <h1 className="text-3xl font-bold mb-2">My Courses</h1>
              <p className="text-gray-400">Manage, edit, and ask questions about your courses.</p>
            </div>
            <Button
              onClick={() => setIsAddDialogOpen(true)}
              className="btn-gradient rounded-full px-5 py-3 h-auto font-semibold whitespace-nowrap"
            >
              <Plus className="w-5 h-5 mr-2" />
              Create New Course
            </Button>
          </div>

          {/* Search and View Toggle */}
          <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between mb-8">
            <div className="relative flex-1 max-w-md w-full">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
              <Input
                placeholder="Search your courses..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-12 h-12 bg-white/5 border-white/10 rounded-xl text-white placeholder:text-gray-500 focus:border-purple-500/50 focus:ring-purple-500/20 w-full"
              />
            </div>
            <ToggleGroup type="single" value={viewMode} onValueChange={(value) => value && setViewMode(value)}>
              <ToggleGroupItem 
                value="grid" 
                className="data-[state=on]:bg-purple-500/20 data-[state=on]:text-white border border-white/10 hover:bg-white/5"
              >
                <Grid3x3 className="w-4 h-4 mr-2" />
                Grid
              </ToggleGroupItem>
              <ToggleGroupItem 
                value="list"
                className="data-[state=on]:bg-purple-500/20 data-[state=on]:text-white border border-white/10 hover:bg-white/5"
              >
                <List className="w-4 h-4 mr-2" />
                List
              </ToggleGroupItem>
            </ToggleGroup>
          </div>

          {/* Course Grid/List */}

          {isLoading ? (
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
              {[1, 2, 3].map(i => (
                <div key={i} className="glass-card rounded-2xl p-6 animate-pulse">
                  <div className="w-12 h-12 rounded-xl bg-white/10 mb-5" />
                  <div className="h-6 bg-white/10 rounded mb-3 w-3/4" />
                  <div className="h-4 bg-white/5 rounded w-full" />
                </div>
              ))}
            </div>
          ) : filteredCourses.length === 0 ? (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="border-2 border-dashed border-white/10 rounded-2xl py-24"
            >
              <div className="text-center">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-pink-500/10 via-purple-500/10 to-blue-500/10 flex items-center justify-center mx-auto mb-6">
                  <BookOpen className="w-8 h-8 text-gray-500" />
                </div>
                <h3 className="text-xl font-semibold mb-2 text-gray-300">No courses created yet</h3>
                <p className="text-gray-500 mb-6 max-w-md mx-auto">
                  Ready to share your knowledge? Create your first course now.
                </p>
                <Button
                  onClick={() => setIsAddDialogOpen(true)}
                  className="btn-gradient rounded-full px-6 py-3 h-auto font-semibold"
                >
                  <Plus className="w-4 h-4 mr-2" />
                  Create a Course
                </Button>
              </div>
            </motion.div>
          ) : (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className={viewMode === "grid" ? "grid md:grid-cols-2 lg:grid-cols-3 gap-6" : "space-y-4"}
            >
              <AnimatePresence>
                {filteredCourses.map((course, index) => (
                  <motion.div
                    key={course.id}
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.9 }}
                    transition={{ delay: index * 0.05 }}
                    whileHover={{ y: -5, scale: 1.02 }}
                    className="group"
                  >
                    <Link to={createPageUrl(`CourseChat?id=${course.id}`)}>
                      <div className="glass-card rounded-2xl p-6 h-full cursor-pointer hover:border-purple-500/30 transition-all duration-300 hover:neon-glow relative">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={(e) => requestDeleteCourse(e, course)}
                          className="absolute top-4 right-4 opacity-70 hover:opacity-100 text-gray-300 hover:text-red-400 hover:bg-red-500/10"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                        <div className={viewMode === "grid" ? "" : "flex items-start gap-4"}>
                          <div className={`${viewMode === "grid" ? "w-12 h-12 mb-5" : "w-12 h-12"} rounded-xl bg-gradient-to-br ${course.color || GRADIENT_COLORS[0]} flex items-center justify-center shrink-0`}>
                            <BookOpen className="w-6 h-6 text-white" />
                          </div>
                          <div className="flex-1">
                            <h3 className="text-lg font-semibold mb-2 group-hover:gradient-text transition-all">
                              {course.name}
                            </h3>
                            {course.description && (
                              <p className="text-gray-400 text-sm line-clamp-2 mb-4">
                                {course.description}
                              </p>
                            )}
                            <div className="flex items-center justify-end mt-4 text-purple-400 opacity-0 group-hover:opacity-100 transition-opacity">
                              <span className="text-sm font-medium mr-1">Open</span>
                              <ChevronRight className="w-4 h-4" />
                            </div>
                          </div>
                        </div>
                      </div>
                    </Link>
                  </motion.div>
                ))}
              </AnimatePresence>
            </motion.div>
          )}
        </div>
      </main>

      <AlertDialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <AlertDialogContent className="bg-[#131313] border-white/10 text-white">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete course?</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-400">
              This will permanently delete <span className="text-white font-medium">{courseToDelete?.name}</span> and all its content and chat history.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              className="border-white/10 bg-white/5 text-white hover:bg-white/10"
              onClick={() => setCourseToDelete(null)}
            >
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700"
              onClick={(e) => {
                e.preventDefault();
                if (!courseToDelete?.id || deleteCourseMutation.isPending) return;
                deleteCourseMutation.mutate(courseToDelete.id);
              }}
            >
              {deleteCourseMutation.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Add Course Dialog */}
      <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
        <DialogContent className="bg-[#131313] border-white/10 text-white max-w-md">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold">Add New Course</DialogTitle>
          </DialogHeader>
          <div className="space-y-5 pt-4">
            <div>
              <Label className="text-gray-300 mb-2 block">Course Name *</Label>
              <Input
                placeholder="e.g., Introduction to Physics"
                value={newCourse.name}
                onChange={(e) => setNewCourse({ ...newCourse, name: e.target.value })}
                className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50"
              />
            </div>
            <div>
              <Label className="text-gray-300 mb-2 block">Description</Label>
              <Textarea
                placeholder="What is this course about?"
                value={newCourse.description}
                onChange={(e) => setNewCourse({ ...newCourse, description: e.target.value })}
                className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50 min-h-[100px]"
              />
            </div>
            <Button
              onClick={handleCreateCourse}
              disabled={!newCourse.name.trim() || createCourseMutation.isPending}
              className="w-full btn-gradient rounded-xl h-12 font-semibold"
            >
              {createCourseMutation.isPending ? "Creating..." : "Create Course"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}