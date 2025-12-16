/* eslint-disable react/prop-types */
import { Link, useLocation, useNavigate } from "react-router-dom";
import { createPageUrl } from "@/utils";
import { client } from "@/api/client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { BookOpen, User, LogOut, Menu } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { toast } from "@/components/ui/use-toast";

export default function Navbar({ onMenuClick, showMenu = false, authVariant = "login" }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const { data: user } = useQuery({
    queryKey: ["currentUser"],
    queryFn: () => client.auth.me(),
    retry: false,
  });

  const userLabel = user?.display_name || user?.email || "";

  const handleLogout = () => {
    client.auth
      .logout()
      .catch(() => {
        // If logout fails, still treat as logged-out in UI.
      })
      .finally(() => {
        queryClient.invalidateQueries({ queryKey: ["currentUser"] });
        navigate("/");
      });
  };

  const handleDeleteAccount = async () => {
    setIsDeleting(true);
    try {
      await client.auth.deleteMe();
      await queryClient.invalidateQueries({ queryKey: ["currentUser"] });
      toast({ title: "Account deleted", description: "Your account and data were permanently deleted." });
      navigate("/");
    } catch (e) {
      const msg =
        e?.data?.detail ||
        (typeof e?.message === "string" ? e.message : null) ||
        "Failed to delete account";
      toast({ title: "Couldn’t delete account", description: msg });
    } finally {
      setIsDeleting(false);
      setDeleteOpen(false);
    }
  };

  return (
    <nav className="relative z-10 px-6 lg:px-16 py-6 border-b border-white/5">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        <Link to={createPageUrl("Home")}>
          <motion.div 
            whileHover={{ scale: 1.02 }}
            className="flex items-center gap-3 cursor-pointer"
          >
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-pink-500 via-purple-500 to-blue-500 flex items-center justify-center">
              <BookOpen className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold tracking-tight">ClassMate</span>
          </motion.div>
        </Link>

        <div className="flex items-center gap-3">
          {user && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="flex items-center gap-3 px-3 py-2 h-auto hover:bg-white/5">
                  <div className="w-9 h-9 rounded-full bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center">
                    <User className="w-5 h-5 text-white" />
                  </div>
                  <span className="text-sm font-medium hidden sm:block">{userLabel}</span>
                </Button>
              </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48 bg-[#131313] border-white/10 text-white">
              <div className="px-2 py-2">
                <p className="text-sm font-medium">Signed in</p>
                {user?.display_name && <p className="text-xs text-gray-300">{user.display_name}</p>}
                <p className="text-xs text-gray-400">{user.email}</p>
              </div>
              <DropdownMenuSeparator className="bg-white/10" />
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  setDeleteOpen(true);
                }}
                className="cursor-pointer text-red-400 focus:text-red-400 focus:bg-red-500/10"
              >
                Delete account
              </DropdownMenuItem>
              <DropdownMenuItem onClick={handleLogout} className="cursor-pointer text-red-400 focus:text-red-400 focus:bg-red-500/10">
                <LogOut className="w-4 h-4 mr-2" />
                Logout
              </DropdownMenuItem>
            </DropdownMenuContent>
            </DropdownMenu>
          )}
          {!user && authVariant !== "none" && (
            <Link
              to={`/${authVariant}?next=${encodeURIComponent(`${location.pathname}${location.search}`)}`}
              className="hidden sm:block"
            >
              <Button className="btn-gradient rounded-full px-5 py-3 h-auto font-semibold whitespace-nowrap">
                {authVariant === "signup" ? "Sign up" : "Login"}
              </Button>
            </Link>
          )}
          {showMenu && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onMenuClick}
              className="text-gray-400 hover:text-white hover:bg-white/5"
            >
              <Menu className="w-5 h-5" />
            </Button>
          )}
        </div>
      </div>

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent className="bg-[#131313] border-white/10 text-white">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete account?</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-400">
              This permanently deletes your account and all your courses and materials. This action can’t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting} className="border-white/10 bg-white/5 text-white hover:bg-white/10">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={isDeleting}
              onClick={(e) => {
                e.preventDefault();
                handleDeleteAccount();
              }}
              className="bg-red-500 hover:bg-red-600"
            >
              {isDeleting ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </nav>
  );
}