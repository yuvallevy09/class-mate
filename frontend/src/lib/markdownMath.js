import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

/**
 * Shared react-markdown plugin config for chat/summary rendering.
 *
 * `remarkMath` parses `$...$` (inline) and `$$...$$` (display) LaTeX, and
 * `rehypeKatex` renders it to HTML via KaTeX. `throwOnError: false` keeps a
 * malformed or half-streamed expression from crashing the render — KaTeX shows
 * it in an error color and recovers once the full expression arrives.
 *
 * The KaTeX stylesheet is imported once globally in `main.jsx`.
 */
export const MARKDOWN_REMARK_PLUGINS = [remarkGfm, remarkMath];

export const MARKDOWN_REHYPE_PLUGINS = [
  [rehypeKatex, { throwOnError: false, strict: false }],
];

/**
 * Promote single-line `$$…$$` blocks to proper display math.
 *
 * `remark-math` only renders `$$` as a centered display block when the
 * delimiters sit on their own lines; a single-line `$$x$$` (which is what the
 * LLM emits) is parsed as *inline* math instead. This rewrites a line that is
 * exactly `$$…$$` into the block form so long equations render centered and on
 * their own line (and pick up the `.katex-display` scroll styling).
 *
 * Only whole-line, single-`$$…$$` matches are touched: inline `$x$`, an
 * already-block `$$` on its own line, and `$$` mid-sentence are all left alone.
 */
export function normalizeDisplayMath(text) {
  const s = String(text ?? "");
  if (!s.includes("$$")) return s;
  return s.replace(
    /^[ \t]*\$\$[ \t]*([^\n]*?)[ \t]*\$\$[ \t]*$/gm,
    (whole, body) => (body && !body.includes("$$") ? `$$\n${body}\n$$` : whole)
  );
}
