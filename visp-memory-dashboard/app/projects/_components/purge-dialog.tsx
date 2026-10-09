import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { Project } from "@/lib/types"

interface PurgeDialogProps {
  target: Project | null
  preview: Record<string, number | string> | null
  confirmation: string
  saving: boolean
  onConfirmation: (value: string) => void
  onCancel: () => void
  onPurge: () => void
}

export function PurgeDialog({ target, preview, confirmation, saving, onConfirmation, onCancel, onPurge }: PurgeDialogProps) {
  return (
    <Dialog open={Boolean(target)} onOpenChange={(open) => { if (!open) onCancel() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Permanently purge project</DialogTitle>
          <DialogDescription>This removes the project and scoped memories after creating a server-side backup. It cannot be undone from the dashboard.</DialogDescription>
        </DialogHeader>
        {preview ? (
          <dl className="well grid grid-cols-3 gap-3 rounded-xl p-3 text-sm">
            <div><dt className="text-muted-foreground">Memories</dt><dd className="font-semibold tabular-nums">{preview.memories}</dd></div>
            <div><dt className="text-muted-foreground">Intents</dt><dd className="font-semibold tabular-nums">{preview.intents}</dd></div>
            <div><dt className="text-muted-foreground">Links</dt><dd className="font-semibold tabular-nums">{preview.relationships}</dd></div>
          </dl>
        ) : null}
        <div className="space-y-2">
          <Label htmlFor="purge-confirmation">Type <span className="font-mono">{target?.id}</span> to confirm</Label>
          <Input id="purge-confirmation" value={confirmation} onChange={(event) => onConfirmation(event.target.value)} autoComplete="off" className="font-mono" />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>Cancel</Button>
          <Button variant="destructive" onClick={onPurge} disabled={saving || confirmation !== target?.id}>Purge project</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
