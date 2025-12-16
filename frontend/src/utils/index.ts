


export function createPageUrl(pageName: string) {
    // IMPORTANT: only normalize the path portion. Query params are case-sensitive
    // when read via URLSearchParams.get(), so preserve the query string exactly.
    const qIndex = pageName.indexOf("?");
    const rawPath = qIndex >= 0 ? pageName.slice(0, qIndex) : pageName;
    const rawQuery = qIndex >= 0 ? pageName.slice(qIndex) : "";

    const path = rawPath.toLowerCase().replace(/ /g, "-");
    return "/" + path + rawQuery;
}