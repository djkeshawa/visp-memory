import type { Memory } from "./types"

export type TrustTone = "neutral" | "info" | "success" | "warning" | "danger"

export interface TrustFlag {
  label: string
  tone: TrustTone
}

/**
 * Review signals a reader should see before relying on a memory. Derived only from
 * what the server reports — nothing here asserts that a memory is correct.
 */
export function memoryTrustFlags(memory: Memory, now = Date.now()): TrustFlag[] {
  const flags = memory.qualityFlags || []
  const has = (pattern: RegExp) => flags.some((flag) => pattern.test(flag))
  const expired = !!memory.validTo && Date.parse(memory.validTo) < now
  const result: TrustFlag[] = []
  if (has(/contradict|conflict/) || !!memory.metadata?.conflict) result.push({ label: "Possible conflict", tone: "danger" })
  if (expired) result.push({ label: "Validity ended", tone: "warning" })
  else if (has(/stale|decay/)) result.push({ label: "May be stale", tone: "warning" })
  if (has(/duplicate/)) result.push({ label: "Possible duplicate", tone: "warning" })
  if (memory.tags?.includes("provenance:external")) result.push({ label: "External · excluded from briefs", tone: "neutral" })
  if (memory.epistemicStatus === "unknown") result.push({ label: "Unverified", tone: "neutral" })
  return result
}

export function evidenceLabel(memory: Memory): string | null {
  const count = memory.evidenceIds?.length || 0
  return count ? `${count} evidence` : null
}

export function shortDate(value: string | null | undefined): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return date.toLocaleDateString(undefined, sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" })
}
