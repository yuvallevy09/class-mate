import React, { useEffect, useMemo, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { TextStyle } from "@tiptap/extension-text-style";
import FontFamily from "@tiptap/extension-font-family";
import { Extension } from "@tiptap/core";
import {
  Bold,
  Italic,
  Underline as UnderlineIcon,
  List,
  ListOrdered,
  Quote,
  Code2,
  Undo2,
  Redo2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const FontSize = Extension.create({
  name: "fontSize",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontSize: {
            default: null,
            parseHTML: (element) => element.style.fontSize || null,
            renderHTML: (attributes) => {
              if (!attributes.fontSize) return {};
              return { style: `font-size: ${attributes.fontSize}` };
            },
          },
        },
      },
    ];
  },
  addCommands() {
    return {
      setFontSize:
        (fontSize) =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontSize }).run(),
      unsetFontSize:
        () =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontSize: null }).run(),
    };
  },
});

const FONT_FAMILIES = [
  // NOTE: Radix Select forbids empty-string item values. We still use empty string internally
  // to mean "unset" (so the Select can show its placeholder), but items must use non-empty values.
  { label: "Default", value: "__default__" },
  { label: "System UI", value: "system-ui" },
  { label: "Inter", value: "Inter" },
  { label: "Roboto", value: "Roboto, system-ui, -apple-system, Segoe UI, sans-serif" },
  { label: "Arial", value: "Arial, Helvetica, sans-serif" },
  { label: "Helvetica", value: "Helvetica, Arial, sans-serif" },
  { label: "Georgia", value: "Georgia, serif" },
  { label: "Times", value: "\"Times New Roman\", Times, serif" },
  { label: "Merriweather", value: "Merriweather, Georgia, serif" },
  { label: "Mono (system)", value: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace" },
  { label: "Courier New", value: "\"Courier New\", Courier, ui-monospace, monospace" },
];

const FONT_SIZES = [
  { label: "10", value: "10px" },
  { label: "11", value: "11px" },
  { label: "12", value: "12px" },
  { label: "13", value: "13px" },
  { label: "14", value: "14px" },
  { label: "16", value: "16px" },
  { label: "18", value: "18px" },
  { label: "20", value: "20px" },
  { label: "24", value: "24px" },
  { label: "28", value: "28px" },
  { label: "32", value: "32px" },
  { label: "36", value: "36px" },
];

function iconButtonClass(isActive) {
  return [
    "h-9 w-9 rounded-lg border inline-flex items-center justify-center leading-none",
    isActive ? "bg-purple-500/20 text-white border-purple-500/30" : "bg-white/0 text-gray-300 border-white/10",
    "hover:bg-white/5",
  ].join(" ");
}

export default function RichTextEditor({
  initialContent,
  onChange,
  placeholder = "Write your notes here...",
}) {
  // Keep toolbar active states in sync with cursor/selection changes.
  const [, bump] = useState(0);
  const content = useMemo(() => {
    // TipTap accepts JSON doc, HTML, or string. We prefer JSON.
    return initialContent || { type: "doc", content: [{ type: "paragraph" }] };
  }, [initialContent]);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
      }),
      TextStyle,
      FontFamily,
      FontSize,
    ],
    content,
    editorProps: {
      attributes: {
        class:
          [
            // Make the editor fill available height and scroll internally.
            "h-full min-h-0 max-h-full w-full rounded-xl bg-white/5 border border-white/10 p-4 text-white outline-none overflow-y-auto",
            "text-sm leading-relaxed",
            // Typography polish (explicit, so it works regardless of global CSS/typography plugin).
            "[&_p]:my-2",
            "[&_h1]:mt-4 [&_h1]:mb-2 [&_h1]:text-2xl [&_h1]:font-semibold [&_h1]:tracking-tight",
            "[&_h2]:mt-4 [&_h2]:mb-2 [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:tracking-tight",
            "[&_h3]:mt-3 [&_h3]:mb-2 [&_h3]:text-lg [&_h3]:font-semibold",
            "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-6",
            "[&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-6",
            "[&_li]:my-1",
            "[&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-white/15 [&_blockquote]:pl-4 [&_blockquote]:text-gray-200 [&_blockquote]:italic",
            "[&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:bg-black/40 [&_pre]:p-4",
            "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
            "[&_code]:rounded [&_code]:bg-white/10 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.95em]",
          ].join(" "),
      },
    },
    onUpdate: ({ editor: ed }) => {
      onChange?.(ed.getJSON());
      bump((x) => x + 1);
    },
    onSelectionUpdate: () => {
      bump((x) => x + 1);
    },
    onFocus: () => {
      bump((x) => x + 1);
    },
    onBlur: () => {
      bump((x) => x + 1);
    },
  });

  // TipTap initializes once; hydrate from initialContent when it becomes available.
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (!editor) return;
    if (hydratedRef.current) return;
    if (!initialContent) return;
    try {
      editor.commands.setContent(initialContent, false);
      hydratedRef.current = true;
    } catch {
      // ignore
    }
  }, [editor, initialContent]);

  if (!editor) return null;

  const currentFont =
    editor.getAttributes("textStyle")?.fontFamily || "";
  const currentSize =
    editor.getAttributes("textStyle")?.fontSize || "";

  const hasText = Boolean(editor.getText().trim());

  return (
    <div className="flex flex-col h-full min-h-0 gap-3">
      {/* Toolbar (always visible) */}
      <div className="flex flex-wrap gap-2 items-center shrink-0">
        <div className="flex gap-2 items-center">
          <Select
            value={String(currentFont)}
            onValueChange={(v) => {
              if (v === "__default__") editor.chain().focus().unsetFontFamily().run();
              else editor.chain().focus().setFontFamily(v).run();
            }}
          >
            <SelectTrigger className="w-[160px] bg-white/5 border-white/10 text-white">
              <SelectValue placeholder="Font" />
            </SelectTrigger>
            <SelectContent className="bg-[#131313] border-white/10 text-white">
              {FONT_FAMILIES.map((f) => (
                <SelectItem key={f.label} value={f.value} className="focus:bg-white/10">
                  {f.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={String(currentSize)}
            onValueChange={(v) => {
              if (v === "__auto__") editor.chain().focus().unsetFontSize().run();
              else editor.chain().focus().setFontSize(v).run();
            }}
          >
            <SelectTrigger className="w-[96px] bg-white/5 border-white/10 text-white">
              <SelectValue placeholder="Size" />
            </SelectTrigger>
            <SelectContent className="bg-[#131313] border-white/10 text-white">
              <SelectItem value="__auto__" className="focus:bg-white/10">
                Auto
              </SelectItem>
              {FONT_SIZES.map((s) => (
                <SelectItem key={s.value} value={s.value} className="focus:bg-white/10">
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="h-6 w-px bg-white/10 mx-1" />

        <div className="flex gap-2 items-center">
          <button
            type="button"
            className={iconButtonClass(editor.isActive("bold"))}
            onClick={() => editor.chain().focus().toggleBold().run()}
            aria-label="Bold"
          >
            <Bold className="w-4 h-4" />
          </button>
          <button
            type="button"
            className={iconButtonClass(editor.isActive("italic"))}
            onClick={() => editor.chain().focus().toggleItalic().run()}
            aria-label="Italic"
          >
            <Italic className="w-4 h-4" />
          </button>
          <button
            type="button"
            className={iconButtonClass(editor.isActive("underline"))}
            onClick={() => editor.chain().focus().toggleUnderline().run()}
            aria-label="Underline"
          >
            <UnderlineIcon className="w-4 h-4" />
          </button>
        </div>

        <div className="h-6 w-px bg-white/10 mx-1" />

        <div className="flex gap-2 items-center">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className={`h-9 px-3 border border-white/10 hover:bg-white/5 ${
              editor.isActive("heading", { level: 1 }) ? "bg-purple-500/20 text-white" : "text-gray-300"
            }`}
            onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
          >
            H1
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className={`h-9 px-3 border border-white/10 hover:bg-white/5 ${
              editor.isActive("heading", { level: 2 }) ? "bg-purple-500/20 text-white" : "text-gray-300"
            }`}
            onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
          >
            H2
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className={`h-9 px-3 border border-white/10 hover:bg-white/5 ${
              editor.isActive("heading", { level: 3 }) ? "bg-purple-500/20 text-white" : "text-gray-300"
            }`}
            onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
          >
            H3
          </Button>
        </div>

        <div className="h-6 w-px bg-white/10 mx-1" />

        <div className="flex gap-2 items-center">
          <button
            type="button"
            className={iconButtonClass(editor.isActive("bulletList"))}
            onClick={() => editor.chain().focus().toggleBulletList().run()}
            aria-label="Bulleted list"
          >
            <List className="w-4 h-4" />
          </button>
          <button
            type="button"
            className={iconButtonClass(editor.isActive("orderedList"))}
            onClick={() => editor.chain().focus().toggleOrderedList().run()}
            aria-label="Numbered list"
          >
            <ListOrdered className="w-4 h-4" />
          </button>
          <button
            type="button"
            className={iconButtonClass(editor.isActive("blockquote"))}
            onClick={() => editor.chain().focus().toggleBlockquote().run()}
            aria-label="Blockquote"
          >
            <Quote className="w-4 h-4" />
          </button>
          <button
            type="button"
            className={iconButtonClass(editor.isActive("codeBlock"))}
            onClick={() => editor.chain().focus().toggleCodeBlock().run()}
            aria-label="Code block"
          >
            <Code2 className="w-4 h-4" />
          </button>
        </div>

        <div className="h-6 w-px bg-white/10 mx-1" />

        <div className="flex gap-2 items-center">
          <button
            type="button"
            className={iconButtonClass(false)}
            onClick={() => editor.chain().focus().undo().run()}
            aria-label="Undo"
          >
            <Undo2 className="w-4 h-4" />
          </button>
          <button
            type="button"
            className={iconButtonClass(false)}
            onClick={() => editor.chain().focus().redo().run()}
            aria-label="Redo"
          >
            <Redo2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Editor */}
      <div className="relative flex-1 min-h-[220px] min-h-0">
        <EditorContent editor={editor} className="h-full min-h-0" />
        {/* Placeholder overlay: hide on focus and when any text exists */}
        {!editor.isFocused && !hasText && (
          <div className="pointer-events-none absolute inset-x-0 top-0 p-4 text-gray-500 text-sm">
            {placeholder}
          </div>
        )}
      </div>
    </div>
  );
}

