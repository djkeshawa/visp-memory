"""Human-readable inspection of an existing injection decision."""

from rich.console import Console
from rich.markdown import Markdown

from visp_memory.core.injection import InjectionResult, format_injection
from visp_memory.core.trust import provenance_of


def print_preview(console: Console, result: InjectionResult) -> None:
    """Render selection and withholding without changing the policy or its JSON."""
    if result.abstained:
        console.print(f"Nothing would be injected — {result.reason}", markup=False)
    else:
        console.print(Markdown(format_injection(result)))
        console.print(result.summary(), markup=False)
        console.print("Selected after scope, trust, relevance and budget checks:")
        for row in result.memories:
            score = float(row.get("relevance_score") or 0.0)
            console.print(
                f"  {row.get('id')} | provenance: {provenance_of(row).value} | "
                f"relevance score: {score:.3f}", markup=False,
            )

    console.print(f"{result.considered} candidates considered; not the entire store.")
    console.print("Earlier recall filters may already have removed expired or out-of-scope rows.")
    dropped = [
        (result.dropped_ineligible, "failed temporal validity or scope checks"),
        (result.dropped_quarantined, "quarantined (external or unknown provenance)"),
        (result.dropped_untrusted, "below the trust threshold (age or provenance)"),
        (result.dropped_stale_anchor, "anchored to deleted code"),
        (result.dropped_below_floor, "below relevance or restricted by task/corpus gates"),
        (result.dropped_redundant, "redundant with a selected memory"),
        (result.dropped_over_budget, "over the count or character budget"),
    ]
    for count, reason in dropped:
        if count:
            console.print(f"  {count} {reason}")
    for reason, count in result.eligibility_filter.get("rejection_counts", {}).items():
        console.print(f"    {reason}: {count}", markup=False)
    if result.abstained:
        console.print('Next: use a concrete task and --file; inspect stored records with "audit".')
