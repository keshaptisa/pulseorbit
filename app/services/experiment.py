from __future__ import annotations

from statistics import mean

from app.models import AssessmentResponse


def compare_with_baseline(result: AssessmentResponse) -> dict:
    """Compare optimized selection with the simple 'keep requested start' baseline."""
    baseline = result.windows[0]
    selected = min(result.windows, key=lambda item: (item.score, item.start))
    return {
        "baseline": {"start": baseline.start.isoformat(), "score": baseline.score, "severity": baseline.severity},
        "selected": {"start": selected.start.isoformat(), "score": selected.score, "severity": selected.severity},
        "score_improvement": baseline.score - selected.score,
        "mean_candidate_score": round(mean(window.score for window in result.windows), 2),
        "candidate_count": len(result.windows),
        "metric_note": "Score is an ordinal decision heuristic, not probability of injury or EVA approval.",
    }
