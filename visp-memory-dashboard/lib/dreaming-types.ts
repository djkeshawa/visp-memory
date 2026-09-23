export type DreamProposal = {
  id: string
  kind: "duplicate" | "related" | "conflict" | "expired"
  reason: string
  automatic: boolean
  memory_ids: string[]
  sources: { id: string; content: string }[]
  draft_summary?: string | null
  resolution?: "pending" | "applied" | "undone" | "dismissed"
  action_id?: string
}

export type DreamRun = {
  id?: string
  created_at?: string
  proposals: DreamProposal[]
  scanned: number
  partial: boolean
  proposal_limit_reached: boolean
  pair_limit_reached: boolean
  scheduled?: boolean
}

export type DreamSettings = {
  enabled: boolean
  interval_hours: number
  next_run?: string | null
  last_error?: string | null
}

export type DreamStatus = { settings: DreamSettings; runs: DreamRun[] }
