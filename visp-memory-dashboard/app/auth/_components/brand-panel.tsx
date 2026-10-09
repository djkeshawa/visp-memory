import { StrataMark } from "@/components/strata/layer-glyph"
import { MEMORY_LAYER_CONFIG, STRATA_ORDER } from "@/lib/layers"

const BAR_WIDTHS: Record<(typeof STRATA_ORDER)[number], string> = {
  intent: "w-[38%]",
  semantic: "w-[64%]",
  episodic: "w-full",
  raw: "w-[82%]",
}

export function BrandPanel() {
  return (
    <section
      aria-label="About visp memory"
      className="flex flex-[1_1_420px] flex-col justify-between gap-10 border-b border-sidebar-border bg-sidebar px-4 py-8 sm:px-12 sm:py-14 lg:border-b-0 lg:border-r lg:px-16 lg:py-[72px]"
    >
      <span className="flex items-center gap-2.5">
        <StrataMark className="h-[30px] w-[30px]" />
        <span className="text-[15px] font-semibold">visp memory</span>
      </span>
      <div aria-hidden="true" className="flex max-w-[420px] flex-col gap-3.5">
        {STRATA_ORDER.map((layer) => (
          <span key={layer} className={`block h-[18px] rounded-md ${MEMORY_LAYER_CONFIG[layer].solid} ${BAR_WIDTHS[layer]}`} />
        ))}
      </div>
      <div className="flex max-w-[460px] flex-col gap-2.5">
        <p className="text-[26px] font-semibold leading-tight tracking-tight">What your projects have learned, with the evidence behind it.</p>
        <p className="text-muted-foreground">Memory supplies context and cited knowledge. It does not grant permission or decide readiness.</p>
      </div>
    </section>
  )
}
