import type { MemoryIntelligenceReport } from "@/lib/types"

/** Plain-language rule behind each report section, filled in with the thresholds the server used. */
export function thresholdText(key: string, thresholds: MemoryIntelligenceReport["thresholds"]): string {
  switch (key) {
    case "high_impact_memories": return `Importance of ${thresholds.high_impact_importance ?? "the threshold"} or higher`
    case "fragile_areas": return "Warning, known-issue or fragile-area memories"
    case "stale_intents": return `Active for ${thresholds.stale_intent_days ?? "many"}+ days`
    case "ambiguous_relationships": return `Ambiguous, or a confidence score of ${thresholds.ambiguous_confidence_score ?? "the threshold"} or lower`
    case "isolated_warnings": return "Warnings with no relationship path"
    case "contradiction_candidates": return "Conflict metadata or a contradiction flag"
    case "cross_repo_risks": return "Links across project scopes, or cross-repo notes"
    case "suggested_questions": return "Raised by the sections above"
    default: return ""
  }
}

export function formatAsOf(value: string | null | undefined): string | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}
