import React, { useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { buildCitationModel, normalizeCitationMarkers } from "./citations";
import { buildMarkdownComponents } from "./markdownComponents";
import { useTypewriter } from "./useTypewriter";

/**
 * Bubble-less assistant message: bare markdown with citation pills.
 * History messages render instantly (`animate={false}`); the live turn
 * streams in via the typewriter (`animate`).
 */
export default function AssistantMessage({
  content,
  citations,
  animate = false,
  onOpenVideo,
  onReveal,
  onRevealComplete,
}) {
  const citationModel = useMemo(() => buildCitationModel(citations), [citations]);
  const normalized = useMemo(
    () => normalizeCitationMarkers(content, citations),
    [content, citations]
  );
  const { visibleText, done } = useTypewriter(normalized, {
    enabled: animate,
    onReveal,
    onComplete: onRevealComplete,
  });

  // Shared per-message coordinator so only one citation popover is open.
  const coordinatorRef = useRef({ close: null });

  const components = useMemo(
    () =>
      buildMarkdownComponents({
        citationModel,
        coordinator: coordinatorRef.current,
        onOpenVideo,
        animatePills: animate && !done,
      }),
    [citationModel, onOpenVideo, animate, done]
  );

  return (
    <div className="text-sm lg:text-base leading-relaxed text-gray-100">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {visibleText}
      </ReactMarkdown>
    </div>
  );
}
