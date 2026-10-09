import { GitMerge } from "lucide-react"
import { Stat } from "@/components/strata/primitives"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import type { Library } from "./use-library"

/** Preview and confirm a merge. Confirming is only possible for the current selection and canonical memory. */
export function MergeDialog({ library, selected }: { library: Library; selected: string[] }) {
  const { open, merging, targetId, preview, previewError, closeMerge, executeMerge, refreshPreview } = library.merge
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next && !merging) closeMerge() }}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Merge memories</DialogTitle>
          <DialogDescription>Preserve one canonical memory and move the others into recoverable merged history.</DialogDescription>
        </DialogHeader>
        <label className="block space-y-2 text-sm">
          <span className="font-medium">Canonical memory</span>
          <select
            value={targetId}
            disabled={merging}
            onChange={(event) => void refreshPreview(event.target.value)}
            className="h-11 w-full rounded-[10px] border border-input bg-card px-3 font-mono text-sm"
          >
            {selected.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        {previewError ? <p role="alert" className="text-sm text-destructive">{previewError}</p> : null}
        {preview ? (
          <div className="space-y-4">
            <div className="well grid grid-cols-3 gap-3 rounded-xl p-4">
              <Stat label="Sources" value={preview.memoryIds.length} valueClassName="text-xl" />
              <Stat label="Tokens saved" value={preview.estimatedTokensSaved} valueClassName="text-xl" />
              <Stat label="Links retargeted" value={preview.relationshipRewrites} valueClassName="text-xl" />
            </div>
            {preview.validationErrors.length ? (
              <ul className="space-y-1 rounded-xl border border-destructive/30 bg-destructive/12 p-3 text-sm text-destructive">
                {preview.validationErrors.map((item) => <li key={item}>{item}</li>)}
              </ul>
            ) : null}
            {preview.warnings.length ? (
              <ul className="space-y-1 rounded-xl border border-warning/30 bg-warning/12 p-3 text-sm">
                {preview.warnings.map((item) => <li key={item}>{item}</li>)}
              </ul>
            ) : null}
          </div>
        ) : !previewError ? <p role="status" className="text-sm text-muted-foreground">Preparing merge preview...</p> : null}
        <DialogFooter>
          <Button variant="outline" disabled={merging} onClick={closeMerge}>Cancel</Button>
          <Button onClick={() => void executeMerge()} disabled={!preview || preview.validationErrors.length > 0 || merging}>
            <GitMerge aria-hidden="true" />
            <span>{merging ? "Merging…" : preview?.exactDuplicate ? "Merge exact duplicates" : "Confirm reviewed merge"}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
