"""Fixed-rule, point-in-time backtests for COT positioning features.

The rules are intentionally simple and declared in code. They are evaluated
from the first price observation on or after the Tuesday following the report
date, which avoids using Tuesday report-date prices and common delayed releases.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


ENTITIES = ("Dealer", "Asset_Mgr", "Lev_Funds", "Other")
STRATEGY_NAMES = {
    "FlowTrend": "Flow + Price Trend",
    "CrowdingReversal": "Crowding Reversal",
    "Composite": "Composite",
}


def _robust_z(values: np.ndarray) -> float:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad == 0:
        return 0.0
    return float((values[-1] - median) / (1.4826 * mad))


def _safe_number(value: float | int | None) -> float | int | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def _max_drawdown(equity: pd.Series) -> float | None:
    if equity.empty:
        return None
    drawdown = equity.div(equity.cummax()).sub(1)
    return float(drawdown.min())


def _strategy_metrics(frame: pd.DataFrame, strategy: str) -> dict[str, object]:
    signal = frame[f"{strategy}_Signal"].fillna(0.0)
    returns = frame[f"{strategy}_Return"].dropna()
    active_mask = signal.ne(0) & frame["Forward_1W"].notna()
    active_returns = frame.loc[active_mask, f"{strategy}_Return"]
    equity = frame.loc[returns.index, f"{strategy}_Equity"]
    weeks = len(returns)
    annualized_return = None
    if weeks and not equity.empty and equity.iloc[-1] > 0:
        annualized_return = float(equity.iloc[-1] ** (52 / weeks) - 1)
    volatility = float(returns.std(ddof=0) * math.sqrt(52)) if weeks > 1 else None
    sharpe = None
    if volatility and volatility > 0:
        sharpe = float(returns.mean() * 52 / volatility)
    return {
        "id": strategy,
        "name": STRATEGY_NAMES[strategy],
        "weeks": weeks,
        "active_weeks": int(active_mask.sum()),
        "long_weeks": int(((signal > 0) & frame["Forward_1W"].notna()).sum()),
        "short_weeks": int(((signal < 0) & frame["Forward_1W"].notna()).sum()),
        "win_rate": _safe_number((active_returns > 0).mean() if len(active_returns) else None),
        "avg_active_return": _safe_number(active_returns.mean() if len(active_returns) else None),
        "annualized_return": _safe_number(annualized_return),
        "annualized_volatility": _safe_number(volatility),
        "sharpe": _safe_number(sharpe),
        "max_drawdown": _safe_number(_max_drawdown(equity)),
        "turnover": _safe_number(frame[f"{strategy}_Turnover"].sum()),
        "ending_equity": _safe_number(equity.iloc[-1] if not equity.empty else None),
    }


def _factor_validation(frame: pd.DataFrame) -> list[dict[str, object]]:
    features = {
        "Net/OI": "Position_Pct",
        "4W Flow/OI": "Flow4_Pct",
        "52W Robust Z": "Position_RobustZ52",
        "52W Percentile": "Position_P52",
        "Lagged 4W Price Momentum": "Price_Momentum_4W_Lagged",
    }
    horizons = {
        "1W": "Forward_1W",
        "4W": "Forward_Return_4W",
        "13W": "Forward_Return_13W",
    }
    results: list[dict[str, object]] = []
    for feature_name, feature_column in features.items():
        for horizon, return_column in horizons.items():
            subset = frame[[feature_column, return_column]].dropna()
            correlation = None
            if len(subset) >= 20 and subset[feature_column].nunique() > 1:
                correlation = subset[feature_column].rank().corr(
                    subset[return_column].rank()
                )
            results.append(
                {
                    "feature": feature_name,
                    "horizon": horizon,
                    "observations": len(subset),
                    "rank_ic": _safe_number(correlation),
                }
            )
    return results


def run_entity_backtest(
    joined: pd.DataFrame,
    entity: str,
    transaction_cost_bps: float = 1.0,
) -> dict[str, object]:
    if entity not in ENTITIES:
        raise ValueError(f"Unsupported entity: {entity}")
    frame = joined.copy().sort_values("Date").reset_index(drop=True)
    frame["Position_Pct"] = frame[f"{entity}_Pct"]
    frame["Position_P52"] = frame[f"{entity}_P52"]
    frame["Position_RobustZ52"] = frame[f"{entity}_RobustZ52"]
    frame["Flow1"] = frame[f"{entity}_Flow1"]
    frame["Flow4_Pct"] = frame[f"{entity}_Flow4"].div(frame["OI"]).mul(100)
    frame["Flow4_RobustZ52"] = frame["Flow4_Pct"].rolling(52, min_periods=52).apply(
        _robust_z, raw=True
    )

    # Following-Tuesday close is the research entry. Price momentum is lagged one weekly
    # observation so the same close is not used to both decide and enter.
    frame["Price_Momentum_4W_Lagged"] = frame["Tradable_Close"].pct_change(4).shift(1)
    frame["Price_Momentum_13W_Lagged"] = frame["Tradable_Close"].pct_change(13).shift(1)
    frame["Forward_1W"] = frame["Tradable_Close"].shift(-1).div(frame["Tradable_Close"]).sub(1)

    same_direction = np.sign(frame["Flow4_Pct"]) == np.sign(frame["Price_Momentum_4W_Lagged"])
    flow_active = same_direction & frame["Flow4_RobustZ52"].abs().ge(0.5)
    frame["FlowTrend_Signal"] = np.where(flow_active, np.sign(frame["Flow4_Pct"]), 0.0)

    reversal = np.zeros(len(frame), dtype=float)
    reversal[(frame["Position_P52"] >= 0.90) & (frame["Flow1"] < 0)] = -1.0
    reversal[(frame["Position_P52"] <= 0.10) & (frame["Flow1"] > 0)] = 1.0
    frame["CrowdingReversal_Signal"] = reversal
    frame["Composite_Signal"] = np.sign(
        frame["FlowTrend_Signal"] + frame["CrowdingReversal_Signal"]
    )

    cost_rate = transaction_cost_bps / 10_000
    for strategy in STRATEGY_NAMES:
        signal_column = f"{strategy}_Signal"
        frame[f"{strategy}_Turnover"] = frame[signal_column].diff().abs().fillna(
            frame[signal_column].abs()
        )
        frame[f"{strategy}_Return"] = (
            frame[signal_column] * frame["Forward_1W"]
            - frame[f"{strategy}_Turnover"] * cost_rate
        )
        frame[f"{strategy}_Equity"] = (
            1 + frame[f"{strategy}_Return"].fillna(0.0)
        ).cumprod()

    equity_rows: list[dict[str, object]] = []
    for _, row in frame[frame["Tradable_Date"].notna()].iterrows():
        equity_rows.append(
            {
                "Date": pd.Timestamp(row["Tradable_Date"]).strftime("%Y-%m-%d"),
                **{
                    strategy: _safe_number(row[f"{strategy}_Equity"])
                    for strategy in STRATEGY_NAMES
                },
            }
        )

    latest = frame.iloc[-1]
    current = {
        "cot_date": pd.Timestamp(latest["Date"]).strftime("%Y-%m-%d"),
        "estimated_release_date": pd.Timestamp(latest["Available_Date"]).strftime("%Y-%m-%d"),
        "research_entry_target_date": (
            pd.Timestamp(latest["Entry_Target_Date"]).strftime("%Y-%m-%d")
            if pd.notna(latest.get("Entry_Target_Date"))
            else None
        ),
        "research_entry_date": (
            pd.Timestamp(latest["Tradable_Date"]).strftime("%Y-%m-%d")
            if pd.notna(latest.get("Tradable_Date"))
            else None
        ),
        "entry_pending": bool(pd.isna(latest.get("Tradable_Close"))),
        "position_pct": _safe_number(latest["Position_Pct"]),
        "position_percentile_52w": _safe_number(latest["Position_P52"]),
        "position_robust_z_52w": _safe_number(latest["Position_RobustZ52"]),
        "flow_4w_pct_oi": _safe_number(latest["Flow4_Pct"]),
        "flow_trend_signal": int(latest["FlowTrend_Signal"]),
        "crowding_reversal_signal": int(latest["CrowdingReversal_Signal"]),
        "composite_signal": int(latest["Composite_Signal"]),
    }
    return {
        "entity": entity,
        "method": {
            "entry": "First trading close on or after the Tuesday following the report date",
            "price_momentum": "Four-week price momentum lagged by one weekly observation",
            "transaction_cost_bps": transaction_cost_bps,
            "overfitting_control": "Fixed rules; no in-sample parameter optimization",
        },
        "current": current,
        "strategies": [
            _strategy_metrics(frame, strategy) for strategy in STRATEGY_NAMES
        ],
        "factor_validation": _factor_validation(frame),
        "equity": equity_rows,
    }


def run_market_backtests(joined: pd.DataFrame) -> dict[str, dict[str, object]]:
    return {entity: run_entity_backtest(joined, entity) for entity in ENTITIES}
