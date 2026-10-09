/**
 * Hands a query from the Desk to Recall without putting it in the URL. Memory queries are
 * free text and can be sensitive; a URL would keep them in browser history and server access
 * logs. Session storage is per-tab, and Recall clears the entry once it has run the search.
 */
const KEY = "visp-memory:recall-handoff"

export function stashRecallQuery(query: string): void {
  try {
    sessionStorage.setItem(KEY, query.trim())
  } catch {
    // Storage blocked: Recall simply opens empty.
  }
}

/** Reads the handed-over query without consuming it, so a re-run effect sees it too. */
export function peekRecallQuery(): string {
  try {
    return (sessionStorage.getItem(KEY) || "").trim()
  } catch {
    return ""
  }
}

export function clearRecallQuery(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // Nothing to clear.
  }
}
