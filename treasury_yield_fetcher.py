"""Official U.S. Treasury daily par yield curve data.

The yield series is used as an independent directional check on continuous
Treasury-futures prices. It is not substituted for a futures settlement feed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd
import requests


class TreasuryYieldFetcher:
    BASE_URL = (
        "https://home.treasury.gov/resource-center/data-chart-center/"
        "interest-rates/pages/xml"
    )
    CACHE_MAX_AGE = timedelta(hours=12)
    HEADERS = {
        "User-Agent": "cot-report/0.2 (Treasury futures validation)",
        "Accept": "application/xml,text/xml",
    }
    FIELD_MAP = {
        "NEW_DATE": "Date",
        "BC_2YEAR": "Yield_2Y",
        "BC_5YEAR": "Yield_5Y",
        "BC_10YEAR": "Yield_10Y",
        "BC_30YEAR": "Yield_30Y",
    }
    MARKET_COLUMN = {
        "2Y": "Yield_2Y",
        "5Y": "Yield_5Y",
        "10Y": "Yield_10Y",
        "U10Y": "Yield_10Y",
        "BOND": "Yield_30Y",
        "UBOND": "Yield_30Y",
    }

    def __init__(self, cache_dir: str | Path = "data", session: requests.Session | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.cache_dir / "treasury_yields.csv"
        self.session = session or requests.Session()
        self.session.headers.update(self.HEADERS)
        self._memory_cache: pd.DataFrame | None = None

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

    def _request_year(self, year: int) -> pd.DataFrame:
        response = self.session.get(
            self.BASE_URL,
            params={
                "data": "daily_treasury_yield_curve",
                "field_tdr_date_value": str(year),
            },
            timeout=60,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        namespaces = {
            "atom": "http://www.w3.org/2005/Atom",
            "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
            "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
        }
        rows: list[dict[str, object]] = []
        for properties in root.findall("atom:entry/atom:content/m:properties", namespaces):
            row: dict[str, object] = {}
            for source, target in self.FIELD_MAP.items():
                element = properties.find(f"d:{source}", namespaces)
                row[target] = element.text if element is not None else None
            rows.append(row)
        return self._validate(pd.DataFrame(rows))

    @staticmethod
    def _validate(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or "Date" not in frame:
            return pd.DataFrame()
        clean = frame.copy()
        clean["Date"] = pd.to_datetime(clean["Date"], errors="coerce")
        for column in ("Yield_2Y", "Yield_5Y", "Yield_10Y", "Yield_30Y"):
            if column in clean:
                clean[column] = pd.to_numeric(clean[column], errors="coerce")
        clean = clean.dropna(subset=["Date"])
        clean = clean.drop_duplicates("Date", keep="last")
        return clean.sort_values("Date").reset_index(drop=True)

    def get_history(
        self,
        start_date: str | pd.Timestamp,
        end_date: str | pd.Timestamp,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        cached = self._memory_cache.copy() if self._memory_cache is not None else self._read_cache()

        requested_years = set(range(start.year, end.year + 1))
        cached_years = set(cached["Date"].dt.year.unique()) if not cached.empty else set()
        years_to_fetch = requested_years.difference(cached_years)
        if force_refresh or not self._cache_is_fresh():
            years_to_fetch.add(end.year)

        try:
            additions = [self._request_year(year) for year in sorted(years_to_fetch)]
            additions = [frame for frame in additions if not frame.empty]
            if additions:
                cached = self._validate(pd.concat([cached, *additions], ignore_index=True))
                cached.to_csv(self.cache_path, index=False)
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            if cached.empty:
                raise RuntimeError(f"Unable to load U.S. Treasury yields: {exc}") from exc
            print(f"Treasury yield refresh failed; using cache: {exc}")

        self._memory_cache = cached
        return cached[(cached["Date"] >= start) & (cached["Date"] <= end)].copy()

    def get_market_history(
        self,
        market: str,
        start_date: str | pd.Timestamp,
        end_date: str | pd.Timestamp,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        if market not in self.MARKET_COLUMN:
            raise ValueError(f"Unsupported Treasury yield market: {market}")
        column = self.MARKET_COLUMN[market]
        frame = self.get_history(start_date, end_date, force_refresh=force_refresh)
        if frame.empty:
            return frame
        result = frame[["Date", column]].rename(columns={column: "Yield"}).dropna()
        result["Yield_Change_1D"] = result["Yield"].diff()
        result["Yield_Change_20D"] = result["Yield"].diff(20)
        result["Tenor"] = column.replace("Yield_", "")
        result["Source"] = "U.S. Treasury daily par yield curve"
        return result
