from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backtest import run_market_backtests
from data_fetcher import COTDataFetcher
from price_fetcher import PriceDataFetcher
from treasury_yield_fetcher import TreasuryYieldFetcher


BASE_DIR = Path(__file__).resolve().parent
PERIOD_DAYS = {
    "3M": 92,
    "6M": 183,
    "1Y": 366,
    "3Y": 1_096,
    "5Y": 1_827,
}

app = FastAPI(title="COT Treasury Futures Analytics", version="0.2.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.cache = None

cot_fetcher = COTDataFetcher(cache_dir=BASE_DIR / "data")
price_fetcher = PriceDataFetcher(
    market_config=cot_fetcher.MARKET_CONFIG,
    cache_dir=BASE_DIR / "data" / "prices",
)
yield_fetcher = TreasuryYieldFetcher(cache_dir=BASE_DIR / "data")


def _display_window(
    frame: pd.DataFrame,
    period: str,
    start: str | None,
    end: str | None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    if frame.empty:
        raise ValueError("No COT data is available")
    end_date = pd.Timestamp(end) if end else pd.Timestamp(datetime.now().date())
    if start:
        start_date = pd.Timestamp(start)
    elif period == "ALL":
        start_date = frame["Date"].min()
    elif period in PERIOD_DAYS:
        start_date = end_date - pd.Timedelta(days=PERIOD_DAYS[period])
    else:
        raise ValueError(f"Unsupported period: {period}")
    if start_date > end_date:
        raise ValueError("start must be earlier than end")
    return start_date.normalize(), end_date.normalize()


def _attach_tradeable_prices(cot: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    if cot.empty or prices.empty:
        return cot.copy()
    result = cot.copy().sort_values("Available_Date").reset_index(drop=True)
    result["Available_Date"] = pd.to_datetime(result["Available_Date"]).astype("datetime64[ns]")
    # Use the following Tuesday as a conservative research entry point. This
    # remains after normal Friday releases and common holiday-delayed Monday
    # releases without pretending we know historical intraday timestamps.
    result["Entry_Target_Date"] = result["Date"] + pd.Timedelta(days=7)
    result["Entry_Target_Date"] = pd.to_datetime(result["Entry_Target_Date"]).astype(
        "datetime64[ns]"
    )
    result["_row_id"] = np.arange(len(result))
    price_lookup = prices[["Date", "Close"]].dropna().sort_values("Date")
    price_lookup = price_lookup.rename(columns={"Date": "Tradable_Date", "Close": "Tradable_Close"})
    price_lookup["Tradable_Date"] = pd.to_datetime(price_lookup["Tradable_Date"]).astype(
        "datetime64[ns]"
    )
    result = pd.merge_asof(
        result,
        price_lookup,
        left_on="Entry_Target_Date",
        right_on="Tradable_Date",
        direction="forward",
    )

    # Calendar horizons are measured from the actual research entry date and
    # mapped to the first available trading day on or after each target. They
    # are research labels, not live forecasts.
    for label, days in (("1W", 7), ("4W", 28), ("13W", 91)):
        target = result[["_row_id", "Tradable_Date"]].copy()
        target["Target_Date"] = target["Tradable_Date"] + pd.Timedelta(days=days)
        valid_target = target.dropna(subset=["Target_Date"])
        if valid_target.empty:
            future_close = pd.Series(dtype=float)
        else:
            future = pd.merge_asof(
                valid_target.sort_values("Target_Date"),
                price_lookup,
                left_on="Target_Date",
                right_on="Tradable_Date",
                direction="forward",
            )
            future_close = future.set_index("_row_id")["Tradable_Close"]
        result[f"Forward_Return_{label}"] = result["_row_id"].map(future_close).div(
            result["Tradable_Close"]
        ).sub(1)
    return result.drop(columns="_row_id").sort_values("Date")


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    serial = frame.copy()
    for column in serial.select_dtypes(include=["datetime", "datetimetz"]).columns:
        serial[column] = serial[column].dt.strftime("%Y-%m-%d")
    serial = serial.replace([np.inf, -np.inf], np.nan)
    return serial.astype(object).where(pd.notna(serial), None).to_dict(orient="records")


def _quality_report(
    cot: pd.DataFrame,
    prices: pd.DataFrame,
    yields: pd.DataFrame,
) -> dict[str, object]:
    cot_gaps = int(cot.sort_values("Date")["Date"].diff().dt.days.gt(10).sum())
    price_gaps = int(prices.sort_values("Date")["Date"].diff().dt.days.gt(4).sum())
    if {"Date", "Close"}.issubset(prices.columns) and {"Date", "Yield"}.issubset(
        yields.columns
    ):
        comparison = pd.merge(
            prices[["Date", "Close"]],
            yields[["Date", "Yield"]],
            on="Date",
            how="inner",
        ).sort_values("Date")
    else:
        comparison = pd.DataFrame()
    correlation = None
    observations = 0
    if not comparison.empty:
        comparison["Price_Return"] = comparison["Close"].pct_change()
        comparison["Yield_Change"] = comparison["Yield"].diff()
        recent = comparison[["Price_Return", "Yield_Change"]].dropna().tail(60)
        observations = len(recent)
        if observations >= 20:
            correlation = recent["Price_Return"].corr(recent["Yield_Change"])
    correlation_value = float(correlation) if correlation is not None and np.isfinite(correlation) else None
    checks = {
        # CFTC may omit a market when fewer than 20 reportable traders remain.
        # A very small number of visible gaps is reported but not treated as a
        # failed dataset.
        "cot_weekly_continuity": cot_gaps / max(len(cot), 1) < 0.01,
        "price_has_history": len(prices) >= 250,
        "price_yield_inverse": correlation_value is not None and correlation_value < 0,
    }
    return {
        "status": "PASS" if all(checks.values()) else "REVIEW",
        "checks": checks,
        "cot_rows": len(cot),
        "cot_gaps_over_10_days": cot_gaps,
        "price_rows": len(prices),
        "price_gaps_over_4_days": price_gaps,
        "yield_rows": len(yields),
        "price_yield_60d_correlation": correlation_value,
        "price_yield_observations": observations,
        "interpretation": "Treasury futures prices should generally move inversely to benchmark yields.",
    }


def get_processed_data() -> pd.DataFrame:
    raw = cot_fetcher.get_historical_data()
    return cot_fetcher.process_signals(raw)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/analysis")
async def get_analysis(
    market: str = "10Y",
    period: str = Query("1Y", pattern="^(3M|6M|1Y|3Y|5Y|ALL)$"),
    start: str | None = None,
    end: str | None = None,
):
    if market not in cot_fetcher.MARKET_CONFIG:
        raise HTTPException(status_code=400, detail=f"Unsupported market: {market}")
    try:
        return JSONResponse(content=build_analysis_payload(market, period, start, end))
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def build_analysis_payload(
    market: str,
    period: str,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    if market not in cot_fetcher.MARKET_CONFIG:
        raise ValueError(f"Unsupported market: {market}")

    signals = get_processed_data()
    market_signals = signals[signals["Market"] == market].copy()
    display_start, display_end = _display_window(market_signals, period, start, end)

    # Backtests always use all available COT history. The visible period only
    # controls presentation and never truncates the indicator calculation set.
    research_start = market_signals["Date"].min() - pd.Timedelta(days=120)
    all_prices = price_fetcher.get_history(market, research_start, display_end)
    joined = _attach_tradeable_prices(market_signals, all_prices)

    # Official par yields independently validate continuous-futures direction.
    yield_start = max(display_start - pd.Timedelta(days=120), display_end - pd.DateOffset(years=5))
    market_yields = yield_fetcher.get_market_history(market, yield_start, display_end)

    visible_cot = joined[
        (joined["Date"] >= display_start) & (joined["Date"] <= display_end)
    ].copy()
    visible_prices = all_prices[
        (all_prices["Date"] >= display_start)
        & (all_prices["Date"] <= display_end)
    ].copy()
    visible_yields = market_yields[
        (market_yields["Date"] >= display_start)
        & (market_yields["Date"] <= display_end)
    ].copy()

    if visible_cot.empty:
        raise ValueError("No COT observations exist in the selected period")

    config = cot_fetcher.MARKET_CONFIG[market]
    latest_cot = visible_cot.iloc[-1]
    latest_price = visible_prices.iloc[-1] if not visible_prices.empty else None
    quality = _quality_report(market_signals, all_prices, market_yields)
    backtests = run_market_backtests(joined)
    meta = {
        "market": market,
        "market_name": config["name"],
        "contract_code": config["contract_code"],
        "price_symbol": config["price_symbol"],
        "period": period,
        "display_start": display_start.strftime("%Y-%m-%d"),
        "display_end": display_end.strftime("%Y-%m-%d"),
        "cot_as_of": latest_cot["Date"].strftime("%Y-%m-%d"),
        "estimated_available_date": latest_cot["Available_Date"].strftime("%Y-%m-%d"),
        "price_as_of": latest_price["Date"].strftime("%Y-%m-%d") if latest_price is not None else None,
        "cot_source": "CFTC PRE — TFF Futures Only",
        "price_source": "Yahoo continuous futures (prototype)",
        "yield_source": "U.S. Treasury daily par yield curve",
        "availability_note": "COT availability is estimated as Tuesday + 3 days; holiday releases may differ.",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "quality_status": quality["status"],
    }
    return {
        "meta": meta,
        "cot": _records(visible_cot),
        "prices": _records(visible_prices),
        "yields": _records(visible_yields),
        "quality": quality,
        "backtests": backtests,
    }


@app.get("/api/data")
async def get_data(
    market: str = "10Y",
    period: str = Query("1Y", pattern="^(3M|6M|1Y|3Y|5Y|ALL)$"),
):
    """Backward-compatible COT-only endpoint."""
    if market not in cot_fetcher.MARKET_CONFIG:
        raise HTTPException(status_code=400, detail=f"Unsupported market: {market}")
    try:
        return JSONResponse(content=build_analysis_payload(market, period)["cot"])
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/markets")
async def get_markets():
    return [
        {
            "id": market,
            "name": config["name"],
            "contract_code": config["contract_code"],
            "price_symbol": config["price_symbol"],
        }
        for market, config in cot_fetcher.MARKET_CONFIG.items()
    ]


@app.get("/api/entities")
async def get_entities():
    return [
        {"id": "Dealer", "name": "Dealer / Intermediary"},
        {"id": "Asset_Mgr", "name": "Asset Manager / Institutional"},
        {"id": "Lev_Funds", "name": "Leveraged Funds"},
        {"id": "Other", "name": "Other Reportables"},
    ]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
