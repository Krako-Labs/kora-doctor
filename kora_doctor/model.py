from dataclasses import dataclass, field
from typing import Dict, List, Any


@dataclass
class Finding:
    category: str
    title: str
    reason: str
    confidence: str
    record_ids: List[str] = field(default_factory=list)
    run_ids: List[str] = field(default_factory=list)
    saving_ratio: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "reason": self.reason,
            "confidence": self.confidence,
            "record_ids": self.record_ids,
            "run_ids": self.run_ids,
            "saving_ratio": self.saving_ratio,
        }


@dataclass
class AuditReport:
    records: int
    model_calls: int
    tool_calls: int
    runs: int
    observed_costs: Dict[str, float]
    potential_savings: Dict[str, float]
    findings: List[Finding]
    category_counts: Dict[str, int]
    warnings: List[str]
    cache_metrics: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        optimized = {
            currency: max(0.0, total - self.potential_savings.get(currency, 0.0))
            for currency, total in self.observed_costs.items()
        }
        percentages = {}
        for currency, total in self.observed_costs.items():
            saved = self.potential_savings.get(currency, 0.0)
            percentages[currency] = (saved / total * 100.0) if total > 0 else 0.0
        return {
            "records": self.records,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "runs": self.runs,
            "observed_costs": self.observed_costs,
            "potential_savings": self.potential_savings,
            "estimated_optimized_costs": optimized,
            "potential_savings_percent": percentages,
            "category_counts": self.category_counts,
            "cache_metrics": self.cache_metrics,
            "findings": [f.as_dict() for f in self.findings],
            "warnings": self.warnings,
        }
