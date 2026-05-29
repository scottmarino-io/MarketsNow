"""AI-powered market stress analysis using Claude."""

from datetime import datetime
from typing import Generator, Optional

import pandas as pd

from modules.stress.config import COMPOSITE_INDICATORS, INFORMATIONAL_INDICATORS

SYSTEM_PROMPT = (
    "You are a financial market analyst specializing in systemic risk and market stress monitoring. "
    "You analyze quantitative stress indicators and provide clear, concise insights for financial professionals. "
    "Your analysis is data-driven, references specific values, and avoids investment advice. "
    "Use markdown formatting with bold section headers."
)


def _fmt_row(key: str, cfg: dict, ind_values: dict, ind_scores: dict, show_score: bool) -> Optional[str]:
    val = ind_values.get(key)
    if val is None:
        return None
    val_str = f"{val:{cfg['fmt']}} {cfg['unit']}"
    if show_score and key in ind_scores:
        return f"  • {cfg['name']}: {val_str}  [stress: {ind_scores[key]:.0f}/100]"
    return f"  • {cfg['name']}: {val_str}"


def _build_prompt(
    composite: float,
    stress_label: str,
    ind_scores: dict,
    ind_values: dict,
    hist: pd.Series,
) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    comp_rows = [r for k, c in COMPOSITE_INDICATORS.items() if (r := _fmt_row(k, c, ind_values, ind_scores, True))]
    info_rows = [r for k, c in INFORMATIONAL_INDICATORS.items() if (r := _fmt_row(k, c, ind_values, ind_scores, False))]

    trend = ""
    if len(hist) >= 5:
        d5 = hist.iloc[-1] - hist.iloc[-5]
        trend = f"\nTREND:\n  5-day change: {d5:+.1f} pts"
        if len(hist) >= 21:
            d21 = hist.iloc[-1] - hist.iloc[-21]
            trend += f"  |  21-day change: {d21:+.1f} pts"
        if len(hist) >= 252:
            hi52, lo52 = hist.iloc[-252:].max(), hist.iloc[-252:].min()
            trend += f"\n  52-week range: {lo52:.1f} – {hi52:.1f}"

    return f"""Analyze the current state of financial market stress using the data below.

DATE: {today}
COMPOSITE STRESS INDEX: {composite:.1f} / 100  ({stress_label.upper()})
Scale: 0–25 Low | 25–45 Moderate | 45–65 Elevated | 65–85 High | 85–100 Extreme
{trend}

COMPOSITE INDICATORS (weighted inputs to the stress score):
{chr(10).join(comp_rows) if comp_rows else "  (no data)"}

INFORMATIONAL INDICATORS (context, not in composite score):
{chr(10).join(info_rows) if info_rows else "  (no data)"}

Provide the following sections:

**Executive Summary**
2–3 sentences on the overall stress environment and what the composite reading signals in practical terms.

**Key Stress Drivers**
The 2–3 most elevated readings — reference exact values, explain systemic implications.

**Resilience Signals**
Indicators showing stability or low stress that temper the overall picture.

**Watch List**
2–3 specific metrics or thresholds to monitor over the next days/weeks, with brief rationale for each.

**Historical Context**
If the current reading warrants it, briefly compare to known stress episodes (2008, COVID Mar 2020, 2022 rate shock, etc.).

Be specific, concise, and data-driven. Do not provide investment advice."""


def stream_analysis(
    api_key: str,
    composite: float,
    stress_label: str,
    ind_scores: dict,
    ind_values: dict,
    hist: pd.Series,
) -> Generator[str, None, None]:
    """Yield streamed text chunks of the AI analysis."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_prompt(composite, stress_label, ind_scores, ind_values, hist)

    try:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=2048,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text
    except anthropic.AuthenticationError:
        yield "\n\n⚠️ **Invalid Anthropic API key.** Please check your key and try again."
    except anthropic.RateLimitError:
        yield "\n\n⚠️ **Rate limit reached.** Please wait a moment and try again."
    except Exception as exc:
        yield f"\n\n⚠️ **Analysis failed:** {exc}"
