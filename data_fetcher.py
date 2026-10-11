"""CFTC Traders in Financial Futures (TFF) data ingestion and features.

The Public Reporting Environment API is the primary source. Contract-market
codes are used instead of display names because CFTC has changed display names
over time. A normalized local cache is retained so the dashboard can still
start when the upstream service is temporarily unavailable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests


class COTDataFetcher:
    API_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
    DEFAULT_START = "2006-06-13"
    CACHE_MAX_AGE = timedelta(hours=6)
    PAGE_SIZE = 10_000
    HEADERS = {
        "User-Agent": "cot-report/0.2 (CFTC TFF research dashboard)",
        "Accept": "application/json",
    }

    MARKET_CONFIG: dict[str, dict[str, str]] = {
        "2Y": {
            "name": "2-Year Treasury Note",
            "contract_code": "042601",
            "price_symbol": "ZT=F",
        },
        "5Y": {
            "name": "5-Year Treasury Note",
            "contract_code": "044601",
            "price_symbol": "ZF=F",
        },
        "10Y": {
            "name": "10-Year Treasury Note",
            "contract_code": "043602",
            "price_symbol": "ZN=F",
        },
        "U10Y": {
            "name": "Ultra 10-Year Treasury Note",
            "contract_code": "043607",
            "price_symbol": "TN=F",
        },
        "BOND": {
            "name": "U.S. Treasury Bond",
            "contract_code": "020601",
            "price_symbol": "ZB=F",
        },
        "UBOND": {
            "name": "Ultra U.S. Treasury Bond",
            "contract_code": "020604",
            "price_symbol": "UB=F",
        },
    }
    TREASURY_MARKETS = {key: value["name"] for key, value in MARKET_CONFIG.items()}

    API_FIELDS = {
        "report_date_as_yyyy_mm_dd": "Date",
        "market_and_exchange_names": "Market_Name",
        "cftc_contract_market_code": "Contract_Code",
        "open_interest_all": "OI",
        "dealer_positions_long_all": "Dealer_Long",
        "dealer_positions_short_all": "Dealer_Short",
        "dealer_positions_spread_all": "Dealer_Spread",
        "asset_mgr_positions_long": "Asset_Mgr_Long",
        "asset_mgr_positions_short": "Asset_Mgr_Short",
        "asset_mgr_positions_spread": "Asset_Mgr_Spread",
        "lev_money_positions_long": "Lev_Funds_Long",
        "lev_money_positions_short": "Lev_Funds_Short",
        "lev_money_positions_spread": "Lev_Funds_Spread",
        "other_rept_positions_long": "Other_Long",
        "other_rept_positions_short": "Other_Short",
        "other_rept_positions_spread": "Other_Spread",
        "traders_tot_all": "Traders_Total",
        "conc_gross_le_4_tdr_long": "Conc4_Long",
        "conc_gross_le_4_tdr_short": "Conc4_Short",
    }
    GROUPS = ("Dealer", "Asset_Mgr", "Lev_Funds", "Other")

    def __init__(self, cache_dir: str | Path = "data", session: requests.Session | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.cache_dir / "cot_tff.csv"
        self.session = session or requests.Session()
        self.session.headers.update(self.HEADERS)
        self._memory_cache: pd.DataFrame | None = None

    @property
    def contract_codes(self) -> list[str]:
        return [item["contract_code"] for item in self.MARKET_CONFIG.values()]

    def _cache_is_fresh(self) -> bool:
        if not self.cache_path.exists() or self.cache_path.stat().st_size == 0:
            return False
        modified = datetime.fromtimestamp(self.cache_path.stat().st_mtime)
        return datetime.now() - modified < self.CACHE_MAX_AGE

    def _read_cache(self) -> pd.DataFrame:
        if not self.cache_path.exists() or self.cache_path.stat().st_size == 0:
            return pd.DataFrame()
        try:
            frame = pd.read_csv(self.cache_path, parse_dates=["Date"])
        except (pd.errors.EmptyDataError, ValueError):
            return pd.DataFrame()
        return self._validate(frame)

    def _request_rows(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        code_list = ",".join(f"'{code}'" for code in self.contract_codes)
        where = (
            f"cftc_contract_market_code in({code_list}) "
            f"AND report_date_as_yyyy_mm_dd >= '{start_date}T00:00:00' "
            f"AND report_date_as_yyyy_mm_dd <= '{end_date}T23:59:59'"
        )
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = {
                "$select": ",".join(self.API_FIELDS),
                "$where": where,
                "$order": "report_date_as_yyyy_mm_dd,cftc_contract_market_code",
                "$limit": str(self.PAGE_SIZE),
                "$offset": str(offset),
            }
            response = self.session.get(self.API_URL, params=params, timeout=45)
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list):
                raise ValueError("Unexpected CFTC API response")
            rows.extend(page)
            if len(page) < self.PAGE_SIZE:
                break
            offset += self.PAGE_SIZE
        return rows

    def _normalize(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows).rename(columns=self.API_FIELDS)
        required = {"Date", "Contract_Code", "OI"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"CFTC response is missing required fields: {sorted(missing)}")

        code_to_market = {
            config["contract_code"]: market for market, config in self.MARKET_CONFIG.items()
        }
        frame["Contract_Code"] = frame["Contract_Code"].astype(str).str.strip()
        frame["Market"] = frame["Contract_Code"].map(code_to_market)
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.tz_localize(None)

        text_columns = {"Date", "Market", "Market_Name", "Contract_Code"}
        for column in frame.columns.difference(list(text_columns)):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return self._validate(frame)

    def _validate(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        required = {"Date", "Market", "Contract_Code", "OI"}
        if not required.issubset(frame.columns):
            return pd.DataFrame()
        clean = frame.copy()
        clean["Date"] = pd.to_datetime(clean["Date"], errors="coerce")
        clean = clean.dropna(subset=["Date", "Market", "OI"])
        clean = clean[clean["Market"].isin(self.MARKET_CONFIG)]
        clean = clean[clean["OI"] > 0]
        clean = clean.drop_duplicates(["Market", "Date"], keep="last")
        return clean.sort_values(["Market", "Date"]).reset_index(drop=True)

    def refresh(self, force: bool = False) -> pd.DataFrame:
        if self._memory_cache is not None and not force:
            return self._memory_cache.copy()

        cached = self._read_cache()
        if not force and self._cache_is_fresh() and not cached.empty:
            self._memory_cache = cached
            return cached.copy()

        if cached.empty:
            start_date = self.DEFAULT_START
        else:
            # Re-read two weeks to reconcile late or duplicate publications.
            start_date = (cached["Date"].max() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        try:
            incoming = self._normalize(self._request_rows(start_date, end_date))
            combined = self._validate(pd.concat([cached, incoming], ignore_index=True))
            if combined.empty:
                raise ValueError("CFTC returned no valid Treasury futures rows")
            combined.to_csv(self.cache_path, index=False)
            self._memory_cache = combined
            return combined.copy()
        except (requests.RequestException, ValueError) as exc:
            if not cached.empty:
                print(f"CFTC refresh failed; using cached data: {exc}")
                self._memory_cache = cached
                return cached.copy()
            raise RuntimeError(f"Unable to load CFTC data: {exc}") from exc

    def fetch_year(self, year: int) -> pd.DataFrame:
        data = self.refresh()
        return data[data["Date"].dt.year == year].copy()

    def get_historical_data(
        self,
        years_back: int | None = None,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        data = self.refresh(force=force_refresh)
        if years_back is not None and start_date is None:
            end = pd.Timestamp(end_date or datetime.now().date())
            start_date = end - pd.DateOffset(years=years_back)
        if start_date is not None:
            data = data[data["Date"] >= pd.Timestamp(start_date)]
        if end_date is not None:
            data = data[data["Date"] <= pd.Timestamp(end_date)]
        return data.copy()

    @staticmethod
    def _rolling_percentile(values: np.ndarray) -> float:
        current = values[-1]
        return float(np.mean(values <= current))

    @staticmethod
    def _rolling_robust_z(values: np.ndarray) -> float:
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        if mad == 0:
            return 0.0
        return float((values[-1] - median) / (1.4826 * mad))

    def process_signals(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()

        processed: list[pd.DataFrame] = []
        for _, market_frame in frame.groupby("Market", sort=False):
            group = market_frame.copy().sort_values("Date")
            for entity in self.GROUPS:
                long_col = f"{entity}_Long"
                short_col = f"{entity}_Short"
                if long_col not in group or short_col not in group:
                    continue
                net_col = f"{entity}_Net"
                pct_col = f"{entity}_Pct"
                group[net_col] = group[long_col] - group[short_col]
                group[pct_col] = group[net_col].div(group["OI"]).mul(100)
                group[f"{entity}_Flow1"] = group[net_col].diff(1)
                group[f"{entity}_Flow4"] = group[net_col].diff(4)
                group[f"{entity}_Flow13"] = group[net_col].diff(13)

                rolling = group[pct_col].rolling(52, min_periods=52)
                std = rolling.std(ddof=0).replace(0, np.nan)
                group[f"{entity}_Z52"] = (group[pct_col] - rolling.mean()) / std
                group[f"{entity}_P52"] = rolling.apply(
                    self._rolling_percentile, raw=True
                )
                group[f"{entity}_RobustZ52"] = rolling.apply(
                    self._rolling_robust_z, raw=True
                )

                rolling_156 = group[pct_col].rolling(156, min_periods=156)
                group[f"{entity}_P156"] = rolling_156.apply(
                    self._rolling_percentile, raw=True
                )

            if {"Lev_Funds_Pct", "Asset_Mgr_Pct"}.issubset(group.columns):
                group["Lev_vs_Asset_Diff"] = group["Lev_Funds_Pct"] - group["Asset_Mgr_Pct"]
            # The historical release calendar is not fully published. Tuesday +
            # three days is an explicit estimate used to prevent Tuesday look-ahead.
            group["Available_Date"] = group["Date"] + pd.Timedelta(days=3)
            processed.append(group)

        return pd.concat(processed, ignore_index=True).sort_values(["Market", "Date"])


if __name__ == "__main__":
    fetcher = COTDataFetcher()
    data = fetcher.process_signals(fetcher.get_historical_data())
    print(data.groupby("Market")["Date"].agg(["min", "max", "count"]))
