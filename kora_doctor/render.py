from .model import AuditReport


LABELS = {
    "repeated_tool_retrieval": "Repeated tool/read calls",
    "retry_overhead": "Retry overhead",
    "unused_work": "Unused/dead work",
    "context_amplification": "Context amplification",
    "deterministic_candidate": "Deterministic candidates",
    "duplicate_repeated": "Repeated model calls",
    "cache_reuse": "Cross-run reuse candidates",
    "orchestration_overhead": "Orchestration overhead",
    "smaller_model_candidate": "Smaller-model candidates",
}

DISPLAY_ORDER = (
    "repeated_tool_retrieval",
    "retry_overhead",
    "unused_work",
    "context_amplification",
    "deterministic_candidate",
    "duplicate_repeated",
    "cache_reuse",
    "orchestration_overhead",
    "smaller_model_candidate",
)


def _money(value: float, currency: str) -> str:
    if currency == "USD":
        return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"
    return f"{value:,.4f} {currency}"


def render_text(report: AuditReport, top: int = 8) -> str:
    lines = []
    lines.append("KORA Doctor")
    lines.append("Find execution waste in AI agent runs.")
    lines.append("")
    lines.append(
        f"Observed: {report.records} records · {report.runs} runs · "
        f"{report.model_calls} model calls · {report.tool_calls} tool calls"
    )

    if report.observed_costs:
        for currency, total in report.observed_costs.items():
            saved = report.potential_savings.get(currency, 0.0)
            optimized = max(0.0, total - saved)
            pct = saved / total * 100.0 if total else 0.0
            lines.append(f"Observed cost:                 {_money(total, currency):>12}")
            lines.append(f"Potentially avoidable:         {_money(saved, currency):>12}  ({pct:.0f}%)")
            lines.append(f"Estimated optimized cost:      {_money(optimized, currency):>12}")
    else:
        lines.append("Observed cost:                 not reported")

    if report.cache_metrics:
        metrics = report.cache_metrics
        reuse = metrics.get("read_share_percent", 0.0)
        read = int(metrics.get("cache_read_tokens", 0))
        uncached = int(metrics.get("uncached_input_tokens", 0))
        calls = int(metrics.get("reported_calls", 0))
        lines.append(
            f"Prompt cache reuse:             {reuse:>11.0f}%  "
            f"({read:,} read / {uncached:,} uncached; {calls} calls)"
        )

    lines.append("")
    lines.append("Execution waste candidates")
    lines.append("-----------------------------------------------")
    for key in DISPLAY_ORDER:
        lines.append(f"{LABELS[key]:31} {report.category_counts.get(key, 0):>4}")

    if report.findings:
        lines.append("")
        lines.append("Top findings")
        lines.append("-----------------------------------------------")
        confidence_rank = {"high": 0, "medium": 1, "low": 2}
        category_rank = {category: i for i, category in enumerate(DISPLAY_ORDER)}
        ranked = sorted(
            report.findings,
            key=lambda f: (
                confidence_rank.get(f.confidence, 9),
                category_rank.get(f.category, 99),
                -len(f.record_ids),
            ),
        )
        for finding in ranked[: max(0, top)]:
            lines.append(
                f"[{finding.confidence.upper():6}] {finding.title} "
                f"({len(finding.record_ids)} call{'s' if len(finding.record_ids) != 1 else ''})"
            )
            lines.append(f"         {finding.reason}")

    if report.warnings:
        lines.append("")
        lines.append("Notes")
        lines.append("-----------------------------------------------")
        for warning in report.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    lines.append("Fix execution waste first. Downsize models second.")
    lines.append("KORA Doctor reports candidates with evidence and confidence, not certainty.")
    return "\n".join(lines)
