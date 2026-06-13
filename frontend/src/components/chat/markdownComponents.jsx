import React from "react";
import CitationPill from "./CitationPill";
import { parseCiteHref } from "./citations";

/**
 * Builds the ReactMarkdown components map for assistant messages.
 * Extracted from CourseChat.jsx; the `#cm-cite-N` / legacy `#cm-src-N` link
 * branches render CitationPill instead of the old superscript scroll links.
 */
export function buildMarkdownComponents({
  citationModel,
  coordinator,
  onOpenVideo,
  animatePills = false,
} = {}) {
  return {
    p: ({ children }) => <p className="my-2 whitespace-pre-wrap">{children}</p>,
    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    ul: ({ children }) => <ul className="my-2 ml-5 list-disc space-y-1">{children}</ul>,
    ol: ({ children }) => <ol className="my-2 ml-5 list-decimal space-y-1">{children}</ol>,
    li: ({ children }) => <li className="leading-relaxed">{children}</li>,
    a: ({ children, href, title }) => {
      const h = String(href || "");
      const t = String(title || "");

      // Hide footnote back-reference links (↩, ↩2, ...).
      if (h.includes("fnref") || h.includes("footnote-backref")) {
        return null;
      }

      // Citation markers (normalized `#cm-cite-N[-M…]`, or legacy `#cm-src-N`
      // that escaped normalization) render as lecture chips.
      const citeIndices = parseCiteHref(h);
      if (citeIndices) {
        return (
          <CitationPill
            indices={citeIndices}
            citationModel={citationModel}
            coordinator={coordinator}
            popIn={animatePills}
          />
        );
      }

      const isHashLink = h.startsWith("#");
      const isFootnoteRef = h.includes("fn-") || h.includes("footnote");

      // Footnote refs jump within the page and look like footnote markers.
      if (isHashLink && isFootnoteRef) {
        return (
          <sup className="ml-0.5">
            <a href={h} className="text-purple-300 no-underline hover:text-purple-200">
              {children}
            </a>
          </sup>
        );
      }

      // Regular in-page links should not open a new tab.
      if (isHashLink) {
        return (
          <a
            href={h}
            className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
          >
            {children}
          </a>
        );
      }

      const isVideoHint = t.toLowerCase().startsWith("video:");
      const videoTitle = isVideoHint ? t.slice("video:".length).trim() : null;
      const looksLikeVideo = /\.(mp4|mov|webm|m4v)(\?|$)/i.test(h);

      // Video transcript citations: open the in-app VideoPlayer in a new tab.
      if (isVideoHint) {
        return (
          <a
            href={h}
            target="_blank"
            rel="noreferrer"
            className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
          >
            {children}
          </a>
        );
      }

      // Raw video links (older behavior): keep modal playback.
      if (looksLikeVideo) {
        return (
          <a
            href={h}
            className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
            onClick={(e) => {
              e.preventDefault();
              onOpenVideo?.({ url: h, title: videoTitle || "Video" });
            }}
          >
            {children}
          </a>
        );
      }

      // External links.
      return (
        <a
          href={h}
          target="_blank"
          rel="noreferrer"
          className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
        >
          {children}
        </a>
      );
    },
    blockquote: ({ children }) => (
      <blockquote className="my-3 border-l-2 border-white/15 pl-4 text-gray-200/90">
        {children}
      </blockquote>
    ),
    code: ({ className, children }) => {
      const isBlock = String(className || "").includes("language-");
      if (isBlock) {
        return <code className={className}>{children}</code>;
      }
      return (
        <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.95em]">
          {children}
        </code>
      );
    },
    pre: ({ children }) => (
      <pre className="my-3 overflow-x-auto rounded-xl bg-black/40 p-4 text-sm">{children}</pre>
    ),
    h1: ({ children }) => <h1 className="mt-4 mb-2 text-lg font-semibold">{children}</h1>,
    h2: ({ children, id }) => {
      // remark-gfm footnotes section header uses id="footnote-label"
      if (String(id || "") === "footnote-label") {
        return <div className="mt-4 mb-2 font-semibold">Sources</div>;
      }
      return <h2 className="mt-4 mb-2 text-base font-semibold">{children}</h2>;
    },
    h3: ({ children }) => <h3 className="mt-3 mb-2 text-sm font-semibold">{children}</h3>,
    hr: () => <hr className="my-4 border-white/10" />,
    table: ({ children }) => (
      <div className="my-3 overflow-x-auto rounded-lg border border-white/15">
        <table className="w-full border-collapse text-left text-sm">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-white/[0.06]">{children}</thead>,
    tbody: ({ children }) => <tbody>{children}</tbody>,
    tr: ({ children }) => (
      <tr className="border-b border-white/10 last:border-0">{children}</tr>
    ),
    th: ({ children }) => (
      <th className="px-3 py-2 font-semibold text-gray-100">{children}</th>
    ),
    td: ({ children }) => (
      <td className="border-l border-white/10 px-3 py-2 align-top first:border-l-0 text-gray-200/90">
        {children}
      </td>
    ),
  };
}
