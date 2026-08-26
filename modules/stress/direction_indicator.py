"""Next-day S&P 500 direction indicator powered by Claude Opus."""

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import pandas as pd


def _safe_latest(series: pd.Series) -> Optional[float]:
    """Return the most recent non-null value in a series, or None."""
    if series is None or series.empty:
        return None
    clean = series.dropna()
    return float(clean.iloc[-1]) if not clean.empty else None


def _pct_change_n(series: pd.Series, n: int) -> Optional[float]:
    """Return the n-day percent change (as a decimal, e.g. 0.023 = +2.3%)."""
    if series is None or series.empty:
        return None
    clean = series.dropna()
    if len(clean) < n + 1:
        return None
    old = clean.iloc[-(n + 1)]
    new = clean.iloc[-1]
    if old == 0:
        return None
    return float((new - old) / abs(old))


def _abs_change_n(series: pd.Series, n: int) -> Optional[float]:
    """Return the absolute n-day change."""
    if series is None or series.empty:
        return None
    clean = series.dropna()
    if len(clean) < n + 1:
        return None
    return float(clean.iloc[-1] - clean.iloc[-(n + 1)])


def fetch_market_headlines(max_headlines: int = 10) -> List[str]:
    """Fetch top market/economy headlines from Google News RSS."""
    url = "https://news.google.com/rss/search?q=stock+market+economy+fed&hl=en-US&gl=US&ceid=US:en"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = root.findall(".//item")
        headlines = []
        for item in items[:max_headlines]:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                text = re.sub(r"<[^>]+>", "", title_el.text).strip()
                if text:
                    headlines.append(text)
        return headlines
    except Exception:
        return []


def compute_signals(
    data: dict,
    composite_hist: pd.Series,
    tide: Optional[Dict] = None,
    gamma: Optional[Dict] = None,
) -> Dict:
    """Extract quantitative signals from market data for the direction model.

    `tide` / `gamma` are optional Unusual Whales readings (fetch_market_tide_eod()
    and fetch_gex_levels(..., source="vol") results respectively) — passed as
    explicit dicts rather than merged into `data`, since `data` is an FRED/Yahoo
    pd.Series dict with a different shape than these pre-computed UW scalars.
    Both default to None so this stays backward-compatible with any caller that
    doesn't have a Unusual Whales key configured.
    """

    def get(key):
        return data.get(key, pd.Series(dtype=float))

    sp500 = get("sp500_drawdown")
    sp500_price = get("sp500_price") if "sp500_price" in data else pd.Series(dtype=float)
    if sp500_price.empty:
        sp_1d = None
        sp_5d = None
        sp_21d = None
    else:
        sp_1d = _pct_change_n(sp500_price, 1)
        sp_5d = _pct_change_n(sp500_price, 5)
        sp_21d = _pct_change_n(sp500_price, 21)

    vix = get("vix")
    vxv = get("vxv")
    hy = get("hy_spread")
    curve = get("yield_curve")
    dxy = get("dxy")
    nfci_s = get("nfci")
    fear_greed_s = get("fear_greed")

    vix_level = _safe_latest(vix)
    vxv_level = _safe_latest(vxv)
    if vix_level is not None and vxv_level is not None and vxv_level != 0:
        vix_term_ratio = round(vix_level / vxv_level, 3)
    else:
        vix_term_ratio = None

    signals = {
        "sp500_1d_pct": round(sp_1d * 100, 2) if sp_1d is not None else None,
        "sp500_5d_pct": round(sp_5d * 100, 2) if sp_5d is not None else None,
        "sp500_21d_pct": round(sp_21d * 100, 2) if sp_21d is not None else None,
        "sp500_drawdown_pct": _safe_latest(sp500),
        "vix": vix_level,
        "vix_5d_chg": _abs_change_n(vix, 5),
        "vix_term_ratio": vix_term_ratio,
        "hy_spread": _safe_latest(hy),
        "hy_spread_5d_chg": _abs_change_n(hy, 5),
        "yield_curve_2s10s": _safe_latest(curve),
        "dxy": _safe_latest(dxy),
        "dxy_5d_chg": _abs_change_n(dxy, 5),
        "nfci": _safe_latest(nfci_s),
        "fear_greed": _safe_latest(fear_greed_s),
        "composite_score": float(composite_hist.iloc[-1]) if not composite_hist.empty else None,
        "composite_5d_chg": (
            float(composite_hist.iloc[-1] - composite_hist.iloc[-5])
            if len(composite_hist) >= 5 else None
        ),
        # Unusual Whales — options market positioning (optional, see docstring)
        "mt_net_call_premium": tide.get("net_call_premium") if tide else None,
        "mt_net_put_premium": tide.get("net_put_premium") if tide else None,
        "mt_net_premium_bias": (
            round((tide["net_call_premium"] + tide["net_put_premium"]) / 1_000_000, 1)
            if tide and tide.get("net_call_premium") is not None
            and tide.get("net_put_premium") is not None
            else None
        ),
        "gamma_flip": gamma.get("gamma_flip") if gamma else None,
        "gamma_call_wall": gamma.get("call_wall") if gamma else None,
        "gamma_put_wall": gamma.get("put_wall") if gamma else None,
    }

    return {k: v for k, v in signals.items()}


def _build_direction_prompt(signals: Dict, headlines: List[str]) -> str:
    def fmt(val, suffix="", decimals=2):
        if val is None:
            return "N/A"
        return f"{val:.{decimals}f}{suffix}"

    def pct(val):
        if val is None:
            return "N/A"
        sign = "+" if val >= 0 else ""
        return f"{sign}{val:.2f}%"

    def chg(val, suffix=""):
        if val is None:
            return "N/A"
        sign = "+" if val >= 0 else ""
        return f"{sign}{val:.2f}{suffix}"

    def uw(val, suffix="", decimals=1):
        if val is None:
            return "N/A — UW not configured"
        sign = "+" if val >= 0 else ""
        return f"{sign}{val:.{decimals}f}{suffix}"

    def uw_level(val):
        if val is None:
            return "N/A — UW not configured"
        return f"{val:g}"

    headline_block = ""
    if headlines:
        headline_block = "\nRECENT MARKET HEADLINES:\n" + "\n".join(
            f"  - {h}" for h in headlines
        )
    else:
        headline_block = "\nRECENT MARKET HEADLINES: (unavailable)"

    return f"""You are a quantitative market analyst. Using the signals below, estimate the probability that the S&P 500 will close HIGHER tomorrow than today's close.

QUANTITATIVE SIGNALS:
  S&P 500 Momentum:
    1-day return:     {pct(signals.get('sp500_1d_pct'))}
    5-day return:     {pct(signals.get('sp500_5d_pct'))}
    21-day return:    {pct(signals.get('sp500_21d_pct'))}
    Drawdown from 52w high: {fmt(signals.get('sp500_drawdown_pct'), '%', 1)}

  Volatility:
    VIX (spot):       {fmt(signals.get('vix'), '', 1)}
    VIX 5-day change: {chg(signals.get('vix_5d_chg'))} pts
    VIX/3M-VIX ratio: {fmt(signals.get('vix_term_ratio'), '', 3)}  (>1 = fear spike / backwardation)

  Credit:
    HY OAS spread:     {fmt(signals.get('hy_spread'), '%', 2)}
    HY spread 5d chg:  {chg(signals.get('hy_spread_5d_chg'), '%')}

  Rates:
    2s10s yield curve: {fmt(signals.get('yield_curve_2s10s'), '%', 2)}

  Dollar:
    DXY Broad Index:   {fmt(signals.get('dxy'), '', 1)}
    DXY 5-day change:  {chg(signals.get('dxy_5d_chg'))}

  Financial Conditions:
    NFCI:              {fmt(signals.get('nfci'), '', 3)}

  Sentiment:
    Fear & Greed:      {fmt(signals.get('fear_greed'), '/100', 0)}

  Composite Stress Index:
    Current score:     {fmt(signals.get('composite_score'), '/100', 1)}
    5-day change:      {chg(signals.get('composite_5d_chg'))} pts

  Options Market Positioning (Unusual Whales):
    Market Tide net premium (calls − puts): {uw(signals.get('mt_net_premium_bias'), 'M')}
      (as of last session close — Market Tide revises intraday, so only the
       settled end-of-day reading is used here to avoid noise)
    SPY Gamma Exposure (dealer positioning, in SPY dollar terms — approx.
    1/10 of the S&P 500 index level):
      Gamma flip level:       {uw_level(signals.get('gamma_flip'))}
      Call wall (resistance): {uw_level(signals.get('gamma_call_wall'))}
      Put wall (support):     {uw_level(signals.get('gamma_put_wall'))}
      (intraday-reactive — read as directional-volume basis, not a fixed
       structural level)
{headline_block}

Respond ONLY with a JSON object in this exact format, no other text:
{{
  "probability_up": <integer 0-100>,
  "confidence": "<low|moderate|high>",
  "lean": "<bullish|bearish|neutral>",
  "bull_signals": ["<signal 1>", "<signal 2>", "<signal 3>"],
  "bear_signals": ["<signal 1>", "<signal 2>", "<signal 3>"],
  "reasoning": "<2-3 sentence explanation of the key factors driving the probability estimate>",
  "key_risk": "<single most important risk to monitor over next 24 hours>"
}}

Rules:
- probability_up is an integer from 1 to 99 (never exactly 50; pick a side)
- confidence reflects how clear the signal is: high = signals agree, low = mixed/noisy
- lean must match probability_up: bullish if >50, bearish if <50, neutral only if 45-55
- bull_signals and bear_signals must each have exactly 3 items, short phrases only
- reasoning and key_risk must reference specific values from the data above
- Do NOT add investment advice or disclaimers inside the JSON"""


def _extract_json(raw: str) -> Dict:
    """Parse JSON from the model response, with regex fallback."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _validate_response(parsed: Dict) -> Dict:
    """Coerce and default all required fields."""
    try:
        prob = int(parsed.get("probability_up", 50))
        prob = max(1, min(99, prob))
    except (TypeError, ValueError):
        prob = 50

    confidence = str(parsed.get("confidence", "low")).lower()
    if confidence not in ("low", "moderate", "high"):
        confidence = "low"

    lean = str(parsed.get("lean", "neutral")).lower()
    if lean not in ("bullish", "bearish", "neutral"):
        lean = "bullish" if prob > 50 else "bearish"

    def coerce_signals(val, default_label):
        if isinstance(val, list):
            items = [str(x).strip() for x in val if x][:3]
            while len(items) < 3:
                items.append(default_label)
            return items
        return [default_label, default_label, default_label]

    bull = coerce_signals(parsed.get("bull_signals"), "—")
    bear = coerce_signals(parsed.get("bear_signals"), "—")

    reasoning = str(parsed.get("reasoning", "Insufficient data to generate analysis.")).strip()
    key_risk = str(parsed.get("key_risk", "Data quality or unexpected macro shock.")).strip()

    return {
        "probability_up": prob,
        "confidence": confidence,
        "lean": lean,
        "bull_signals": bull,
        "bear_signals": bear,
        "reasoning": reasoning,
        "key_risk": key_risk,
    }


def generate_direction_forecast(
    api_key: str,
    signals: Dict,
    headlines: List[str],
) -> Dict:
    """
    Call Claude Opus and return a structured direction forecast.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_direction_prompt(signals, headlines)

    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text if response.content else ""
        parsed = _extract_json(raw)
        if not parsed:
            return {"error": "Model returned an empty or unparseable response."}
        return _validate_response(parsed)

    except anthropic.AuthenticationError:
        return {"error": "Invalid Anthropic API key."}
    except anthropic.RateLimitError:
        return {"error": "Rate limit reached — please wait a moment and try again."}
    except Exception as exc:
        return {"error": str(exc)}
