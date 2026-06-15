import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { History, X, MessageSquarePlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import { listCourseConversations, deleteConversation } from "@/api/chat";

const RTF = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** "2 hours ago" / "yesterday" from an ISO timestamp; "" when unparseable. */
function relativeTime(iso) {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const diff = Math.round((then - Date.now()) / 1000); // negative = past
  const abs = Math.abs(diff);
  if (abs < 60) return RTF.format(diff, "second");
  if (abs < 3600) return RTF.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return RTF.format(Math.round(diff / 3600), "hour");
  if (abs < 2592000) return RTF.format(Math.round(diff / 86400), "day");
  if (abs < 31536000) return RTF.format(Math.round(diff / 2592000), "month");
  return RTF.format(Math.round(diff / 31536000), "year");
}

/**
 * Per-lecture conversation switcher: a "History" button that opens a popover
 * listing this video's conversations (newest first). Pick one to load it, start
 * a new one, or delete one. Chat state lives in the parent (VideoPlayer); this
 * component only lists/deletes and calls onSelect/onNew.
 */
export default function VideoConversationSwitcher({
  courseId,
  videoAssetId,
  activeConversationId,
  onSelect,
  onNew,
}) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: conversations = [], isLoading } = useQuery({
    queryKey: ["videoConversations", courseId, videoAssetId],
    queryFn: () => listCourseConversations(courseId, { videoAssetId }),
    enabled: !!courseId && !!videoAssetId && open, // lazy: only when opened
  });

  const deleteMutation = useMutation({
    mutationFn: (conversationId) => deleteConversation(conversationId),
    onSuccess: (_data, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ["videoConversations", courseId, videoAssetId] });
      queryClient.invalidateQueries({ queryKey: ["conversations", courseId] });
      queryClient.removeQueries({ queryKey: ["conversationMessages", String(deletedId)] });
      // Deleting the open thread → reset the chat to a fresh one.
      if (activeConversationId && String(activeConversationId) === String(deletedId)) {
        onNew?.();
      }
    },
  });

  const handleDelete = (e, conversationId) => {
    e?.preventDefault?.();
    e?.stopPropagation?.();
    if (!conversationId || deleteMutation.isPending) return;
    deleteMutation.mutate(conversationId);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="text-gray-400 hover:text-white hover:bg-white/5"
          aria-label="Conversation history"
        >
          <History className="w-5 h-5" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        // z-[70]: the popped-out chat lives in a Dialog (overlay/content z-50).
        className="z-[70] w-72 glass-card border-white/10 p-2"
      >
        <button
          type="button"
          onClick={() => {
            onNew?.();
            setOpen(false);
          }}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left text-gray-300 hover:text-white hover:bg-white/5 transition-colors"
        >
          <MessageSquarePlus className="w-4 h-4 text-purple-400" />
          <span className="text-sm font-medium">New conversation</span>
        </button>

        <div className="my-1 border-t border-white/5" />

        {/* Caps the list so the popover never grows unbounded — scrolls past
            ~6–7 conversations while "New conversation" stays pinned above. */}
        <div className="max-h-72 overflow-y-auto space-y-1 pr-1">
          {isLoading ? (
              <div className="px-3 py-2 text-xs text-gray-500">Loading…</div>
            ) : conversations.length === 0 ? (
              <div className="px-3 py-2 text-xs text-gray-500">
                No conversations for this lecture yet.
              </div>
            ) : (
              conversations.map((c) => {
                const cid = c?.id;
                const isActive =
                  activeConversationId && String(cid) === String(activeConversationId);
                const label = c?.title || "Conversation";
                const when = relativeTime(c?.last_message_at || c?.created_at);
                return (
                  <div
                    key={cid}
                    className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-colors group ${
                      isActive
                        ? "bg-purple-500/20 text-white border border-purple-500/30"
                        : "text-gray-300 hover:text-white hover:bg-white/5"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        onSelect?.(cid);
                        setOpen(false);
                      }}
                      className="flex-1 min-w-0 text-left"
                    >
                      <span className="text-sm font-medium truncate block">{label}</span>
                      {when ? <span className="text-[11px] text-gray-500">{when}</span> : null}
                    </button>
                    <button
                      type="button"
                      aria-label="Delete conversation"
                      title="Delete conversation"
                      onClick={(e) => handleDelete(e, cid)}
                      className="opacity-0 group-hover:opacity-100 transition-opacity rounded-md p-1 text-gray-400 hover:text-red-400 hover:bg-red-500/10"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                );
              })
            )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
