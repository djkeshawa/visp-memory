import { cn } from "@/lib/utils"
import { CHOICES, CHOICE_ORDER, type ChoiceKey } from "./choices"

export function ChoiceCards({ value, onChange }: { value: ChoiceKey; onChange: (key: ChoiceKey) => void }) {
  return (
    <fieldset className="m-0 grid min-w-0 gap-3 border-0 p-0 sm:grid-cols-2 lg:grid-cols-4">
      <legend className="sr-only">Search option</legend>
      {CHOICE_ORDER.map((key) => {
        const item = CHOICES[key]
        const pressed = value === key
        return (
          <label
            key={key}
            className={cn(
              "flex min-h-28 cursor-pointer flex-col items-start gap-2 rounded-2xl border p-4 text-left transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
              pressed ? "border-highlight bg-accent" : "surface hover:border-input",
            )}
          >
            <span className="flex w-full items-center justify-between gap-2 text-[15px] font-semibold">
              {item.title}
              <input type="radio" name="search-choice" value={key} checked={pressed} onChange={() => onChange(key)} className="h-4 w-4 shrink-0 focus-visible:outline-none" />
            </span>
            <span className="text-[13px] leading-5 text-muted-foreground">{item.detail}</span>
            <span className="mt-auto text-xs text-muted-foreground">{item.tag}</span>
          </label>
        )
      })}
    </fieldset>
  )
}
