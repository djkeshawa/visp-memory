"use client"

import { Suspense } from "react"
import { PageHeader } from "@/components/strata/primitives"
import { AppearanceSection } from "./_components/appearance-section"
import { ProviderList } from "./_components/provider-list"
import { SearchSection } from "./_components/search-section"
import { TaskModelSection } from "./_components/task-model-section"
import { useDiagnostics } from "./_components/use-diagnostics"

function SettingsContent() {
  const d = useDiagnostics()
  return (
    <div className="mx-auto max-w-[880px] space-y-6">
      <PageHeader eyebrow="Provider diagnostics and connectivity checks" title="Settings" />

      {d.loadError ? (
        <p role="alert" className="rounded-xl bg-destructive/12 p-4 text-sm text-destructive">{d.loadError}</p>
      ) : null}
      {d.isLoading ? <p role="status" className="surface rounded-xl p-4 text-sm text-muted-foreground">Loading diagnostics...</p> : null}

      {d.embeddingIndex ? (
        <SearchSection
          index={d.embeddingIndex}
          storage={d.storageDiagnostics}
          providers={d.providers}
          reindexResult={d.reindexResult}
          isLoading={d.isLoading}
          isReindexing={d.isReindexing}
          onCheck={() => void d.loadDiagnostics()}
          onReindex={(dryRun) => void d.handleReindex(dryRun)}
        />
      ) : null}

      {d.modelRouting ? (
        <TaskModelSection routing={d.modelRouting} message={d.modelMessage} isTesting={d.isTestingModel} onTest={() => void d.handleModelTest()} />
      ) : null}

      {!d.isLoading && d.providers.length === 0 && !d.loadError ? (
        <p className="surface rounded-xl p-4 text-sm text-muted-foreground">No provider diagnostics are currently available.</p>
      ) : null}
      {d.providers.length > 0 ? <ProviderList providers={d.providers} testing={d.testingProvider} onTest={(name) => void d.handleTest(name)} /> : null}

      <AppearanceSection />
    </div>
  )
}

export default function SettingsPage() {
  return (
    <Suspense fallback={null}>
      <SettingsContent />
    </Suspense>
  )
}
