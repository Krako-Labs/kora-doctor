from .model import AuditReport


LABELS = {
    "duplicate_repeated": "Duplicate/repeated calls",
    "cache_reuse": "Cache/reuse candidates",
    "deterministic_candidate": "Deterministic candidates",
    "smaller_model_candidate": "Smaller-model candidates",
    "orchestration_overhead": "Orchestration overhead",
}


def _money(value: float, currency: str) -> str:
    if currency == "USD":
        return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"
    return f"{value:,.4f} {currency}"


def render_text(report: AuditReport, top: int = 8) -> str:
    lines = []
    lines.append("KORA Doctor")
    lines.append("Find the LLM calls your AI agent may never have needed.")
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

    lines.append("")
    lines.append("Candidates")
    lines.append("-----------------------------------------------")
    for key in (
        "duplicate_repeated",
        "cache_reuse",
        "deterministic_candidate",
        "smaller_model_candidate",
        "orchestration_overhead",
    ):
        lines.append(f"{LABELS[key]:31} {report.category_counts.get(key, 0):>4}")

    if report.findings:
        lines.append("")
        lines.append("Top findings")
        lines.append("-----------------------------------------------")
        confidence_rank = {"medium": 0, "low": 1}
        ranked = sorted(
            report.findings,
            key=lambda f: (confidence_rank.get(f.confidence, 9), -len(f.record_ids), f.category),
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
    lines.append("KORA Doctor reports heuristic candidates, not proof that a call was unnecessary.")
    return "\n".join(lines)
