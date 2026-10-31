import os
import json
import httpx
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")

CLAUDE_MODEL  = "claude-haiku-4-5"
OPENAI_MODEL  = "gpt-4o-mini"


class ExplainRequest(BaseModel):
    ticker:   str
    analysis: dict
    question: Optional[str] = None


@router.post("/explain")
async def explain_trade(req: ExplainRequest):
    prompt = _build_prompt(req.ticker, req.analysis, req.question)

    if ANTHROPIC_API_KEY:
        try:
            text = await _call_claude(prompt)
            return {"explanation": text, "source": "claude"}
        except Exception as e:
            pass

    if OPENAI_API_KEY:
        try:
            text = await _call_openai(prompt)
            return {"explanation": text, "source": "openai"}
        except Exception as e:
            pass

    return {
        "explanation": _rule_based_explanation(req.analysis),
        "source": "rule_based",
    }


@router.post("/explain-indicator")
async def explain_indicator(payload: dict):
    indicator_name = payload.get("indicator", "")
    value          = payload.get("value", "")
    context        = payload.get("context", "")

    explanations = {
        "rsi":    _explain_rsi(float(value) if value else 50),
        "macd":   _explain_macd(context),
        "ma20":   "The 20-day moving average is a short-term trend line. When price is above it, short-term momentum is bullish.",
        "ma50":   "The 50-day moving average is the medium-term trend gauge used by most institutional traders.",
        "ma200":  "The 200-day moving average defines the long-term trend. Above = bull market, below = bear market.",
        "volume": _explain_volume(float(value) if value else 1.0),
        "atr":    "ATR (Average True Range) measures daily volatility. Higher ATR = wider stop needed.",
        "bb":     "Bollinger Bands show price deviation from its mean. Upper band = extended, lower = depressed.",
    }

    name_lower = indicator_name.lower().replace(" ", "")
    for key, val in explanations.items():
        if key in name_lower:
            return {"indicator": indicator_name, "explanation": val}

    return {"indicator": indicator_name, "explanation": f"{indicator_name} measures price momentum and trend strength."}


def _build_prompt(ticker: str, analysis: dict, question: Optional[str]) -> str:
    setup  = analysis.get("setup", {})
    plan   = analysis.get("trade_plan", {})
    preds  = analysis.get("predictions", {})

    summary = f"""
Ticker: {ticker} | Price: ${analysis.get('price', 'N/A')} | Change: {analysis.get('day_change', 0):+.2f}%
Trend: {analysis.get('trend', 'N/A')} (Strength: {analysis.get('trend_strength', 0):.0f}%)
MA20: {analysis.get('ma20', 'N/A')} | MA50: {analysis.get('ma50', 'N/A')} | MA200: {analysis.get('ma200', 'N/A')}
RSI(14): {analysis.get('rsi14', 'N/A')} | MACD: {analysis.get('macd', {}).get('cross', 'N/A')}
Volume Ratio: {analysis.get('volume', {}).get('ratio', 'N/A')}x | ATR%: {analysis.get('atr_pct', 'N/A')}%
Setup: {setup.get('setup_type', 'N/A')} | Confidence: {setup.get('confidence', 0):.0f}%
Quality Score: {plan.get('quality_score', 'N/A')} ({plan.get('quality_grade', 'N/A')})
Direction: {plan.get('direction', 'N/A')}
Entry: ${plan.get('entry_ideal', 'N/A')} | Stop: ${plan.get('stop', 'N/A')} | Target: ${plan.get('target', 'N/A')}
Risk/Reward: {plan.get('risk_reward', 'N/A')}:1
Rules fired: {', '.join(setup.get('rules_fired', []))}
5d prediction: {preds.get('5d', {}).get('expected_pct', 0):+.1f}% ({preds.get('5d', {}).get('signal', 'N/A')})
20d prediction: {preds.get('20d', {}).get('expected_pct', 0):+.1f}% ({preds.get('20d', {}).get('signal', 'N/A')})
"""

    user_q = f"\n\nTrader's question: {question}" if question else ""

    return f"""You are Oryntra, a professional stock analysis assistant. A trader is reviewing this analysis:

{summary}

Provide a concise, professional explanation (3-5 sentences) that:
1. Describes why this setup is {setup.get('setup_type', 'identified')}
2. Highlights the 2-3 most important signals the trader should focus on
3. Explains what would CONFIRM or INVALIDATE this trade
4. States the key risk to watch

Be direct and specific. Use trader language. Do not add generic disclaimers.{user_q}"""


async def _call_claude(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      CLAUDE_MODEL,
                "max_tokens": 600,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        return response.json()["content"][0]["text"]


async def _call_openai(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":      OPENAI_MODEL,
                "max_tokens": 600,
                "messages": [
                    {
                        "role":    "system",
                        "content": "You are Oryntra, a professional stock analysis assistant. Be concise, direct, and use trader language.",
                    },
                    {
                        "role":    "user",
                        "content": prompt,
                    },
                ],
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def _rule_based_explanation(analysis: dict) -> str:
    setup      = analysis.get("setup", {})
    plan       = analysis.get("trade_plan", {})
    ticker     = analysis.get("ticker", "")
    price      = analysis.get("price", 0)
    trend      = analysis.get("trend", "SIDEWAYS")
    rsi        = analysis.get("rsi14", 50) or 50
    setup_type = setup.get("setup_type", "NO_TRADE")
    rules      = setup.get("rules_fired", [])
    direction  = plan.get("direction", "NEUTRAL")
    score      = plan.get("quality_score", 0)
    rr         = plan.get("risk_reward") or 0
    patterns   = ((analysis.get("patterns") or {}).get("recent") or [])[:3]
    preds      = analysis.get("predictions") or {}

    lines = [
        f"**{ticker} @ ${price:.2f} — {setup_type.replace('_', ' ')}**",
        "",
        f"Trend context: {trend.replace('_', ' ').title()}. "
        f"RSI at {rsi:.0f} indicates {'overbought conditions' if rsi > 70 else 'oversold conditions' if rsi < 30 else 'neutral momentum'}.",
        "",
        f"Setup quality: {score:.0f}/100 ({plan.get('quality_grade', 'N/A')}). "
        f"{'This is a high-conviction setup.' if score >= 70 else 'This is a marginal setup — size small.' if score >= 50 else 'Low confidence — consider passing.'}",
        "",
        f"Key signals: {'; '.join(rules[:3]) if rules else 'No strong signals detected'}.",
    ]

    if patterns:
        pattern_bits = []
        for p in patterns:
            name = str(p.get("pattern_name", "PATTERN")).replace("_", " ").title()
            conf = p.get("confidence", 0) or 0
            side = p.get("direction", "NEUTRAL")
            pattern_bits.append(f"{name} ({side}, {conf:.0f}% confidence)")
        lines += ["", f"Pattern read: {'; '.join(pattern_bits)}."]

    if isinstance(preds, dict) and preds:
        five = preds.get("5d", {}) or {}
        twenty = preds.get("20d", {}) or {}
        if five or twenty:
            lines += [
                "",
                f"Estimate context: 5-day expected move {five.get('expected_pct', 0):+.1f}% and 20-day expected move {twenty.get('expected_pct', 0):+.1f}%, adjusted by trend, setup quality, volatility, and detected pattern intensity.",
            ]

    if direction != "NEUTRAL" and plan.get("stop"):
        lines += [
            "",
            f"Trade plan: {direction} entry near ${plan.get('entry_ideal', 0):.2f}, "
            f"stop at ${plan.get('stop', 0):.2f} ({plan.get('risk_pct', 0):.1f}% risk), "
            f"target ${plan.get('target', 0):.2f}. "
            f"Risk/Reward: {rr:.1f}:1.",
        ]

    if setup_type == "NO_TRADE":
        lines.append("\n**No trade recommended.** Wait for a cleaner setup with higher conviction.")


    return "\n".join(lines)


def _explain_rsi(val: float) -> str:
    if val >= 80:   return f"RSI at {val:.0f} is severely overbought. The stock has moved far above its average — mean reversion or stall is likely. Avoid new longs."
    if val >= 70:   return f"RSI at {val:.0f} is overbought. Price is extended. Can remain overbought in strong trends but chasing here is risky."
    if val >= 50:   return f"RSI at {val:.0f} is bullish territory — buyers in control. Good range for trend-following long setups."
    if val >= 30:   return f"RSI at {val:.0f} is approaching oversold. Selling pressure is elevated but a bounce or setup may develop."
    return f"RSI at {val:.0f} is severely oversold. Potential reversal/bounce candidate, but confirm with price action."


def _explain_macd(context: str) -> str:
    if "BULLISH" in context.upper():  return "MACD just crossed above its signal line — a fresh bullish momentum signal. Best when it crosses from below zero."
    if "BEARISH" in context.upper():  return "MACD just crossed below its signal line — a bearish momentum shift. Confirms selling pressure building."
    if "BULL" in context.upper():     return "MACD histogram is positive — momentum currently favors buyers. Not a new cross, but trend is intact."
    return "MACD histogram is negative — sellers have momentum advantage. Avoid new longs until this flips."


def _explain_volume(ratio: float) -> str:
    if ratio >= 2.5:  return f"Volume is {ratio:.1f}x the 20-day average — a major surge. Signals strong institutional conviction behind this move."
    if ratio >= 1.5:  return f"Volume is elevated at {ratio:.1f}x average — above-normal participation backing the price action."
    if ratio >= 0.8:  return f"Volume is normal ({ratio:.1f}x average) — no unusual conviction in either direction."
    return f"Volume is low at {ratio:.1f}x average — weak participation. Be cautious; moves on low volume are less reliable."

