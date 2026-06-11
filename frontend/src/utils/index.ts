


export const GRADIENT_COLORS = [
    "from-pink-500 to-purple-600",
    "from-purple-500 to-blue-600",
    "from-blue-500 to-cyan-500",
    "from-cyan-500 to-teal-500",
    "from-orange-500 to-pink-500",
    "from-violet-500 to-purple-600"
];

function hashString(s: string) {
    let h = 0;
    for (let i = 0; i < s.length; i += 1) {
        h = ((h << 5) - h) + s.charCodeAt(i);
        h |= 0; // keep 32-bit
    }
    return Math.abs(h);
}

// Stable per-course gradient so a course keeps its visual identity across pages.
export function courseGradient(courseId: string | null | undefined) {
    return GRADIENT_COLORS[hashString(String(courseId || "")) % GRADIENT_COLORS.length];
}

export function createPageUrl(pageName: string) {
    // IMPORTANT: only normalize the path portion. Query params are case-sensitive
    // when read via URLSearchParams.get(), so preserve the query string exactly.
    const qIndex = pageName.indexOf("?");
    const rawPath = qIndex >= 0 ? pageName.slice(0, qIndex) : pageName;
    const rawQuery = qIndex >= 0 ? pageName.slice(qIndex) : "";

    const path = rawPath.toLowerCase().replace(/ /g, "-");
    return "/" + path + rawQuery;
}