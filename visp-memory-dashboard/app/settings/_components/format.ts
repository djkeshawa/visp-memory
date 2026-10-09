import type { ProviderConnectionStatus, ProviderDiagnostic } from "@/lib/types"
import type { TrustTone } from "@/lib/memory-trust"

export const PROVIDER_STATUS: Record<ProviderConnectionStatus, { label: string; tone: TrustTone }> = {
  connected: { label: "Connected", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  disabled: { label: "Disabled", tone: "neutral" },
  fallback: { label: "Fallback", tone: "warning" },
  not_configured: { label: "Not configured", tone: "neutral" },
  not_checked: { label: "Not checked", tone: "neutral" },
}

export function formatStatus(value: string) {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

export function formatLastChecked(lastChecked?: string) {
  if (!lastChecked) return "Not checked yet"
  const date = new Date(lastChecked)
  if (Number.isNaN(date.getTime())) return lastChecked
  return date.toLocaleString()
}

export function upsertProvider(providers: ProviderDiagnostic[], provider: ProviderDiagnostic): ProviderDiagnostic[] {
  const exists = providers.some((item) => item.provider === provider.provider)
  const next = exists
    ? providers.map((item) => (item.provider === provider.provider ? { ...item, ...provider } : item))
    : [...providers, provider]
  return next.sort((left, right) => left.provider.localeCompare(right.provider))
}
