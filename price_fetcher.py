"""Daily continuous Treasury futures prices with a replaceable provider.

Yahoo's chart endpoint is used for the prototype because it is accessible
without credentials. Cache and output columns are provider-neutral so a
licensed settlement-price feed can replace it without changing the dashboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests


class PriceDataFetcher:
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    CACHE_MAX_AGE = timedelta(hours=6)
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    def __init__(
        self,
        market_config: dict[str, dict[str, str]],
        cache_dir: str | Path = "data/prices",
        session: requests.Session | None = None,
    ):
        self.market_config = market_config
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self.session.headers.update(self.HEADERS)
        self._memory_cache: dict[str, pd.DataFrame] = {}

    def _cache_path(self, market: str) -> Path:
        return self.cache_dir / f"{market.lower()}_daily.csv"

    def _read_cache(self, market: str) -> pd.DataFrame:
        path = self._cache_path(market)
        if not path.exists() or path.stat().st_size == 0:
            return pd.DataFrame()
        try:
            data = pd.read_csv(path, parse_dates=["Date"])
        except (pd.errors.EmptyDataError, ValueError):
            return pd.DataFrame()
        return self._validate(data)

    def _cache_is_fresh(self, market: str) -> bool:
        path = self._cache_path(market)
        if not path.exists() or path.stat().st_size == 0:
            return False
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        return datetime.now() - modified < self.CACHE_MAX_AGE

    @staticmethod
    def _to_unix(value: str | pd.Timestamp) -> int:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        return int(stamp.timestamp())

    def _request(self, market: str, start_date: str, end_date: str) -> pd.DataFrame:
        if market not in self.market_config:
            raise ValueError(f"Unsupported market: {market}")
        symbol = self.market_config[market]["price_symbol"]
        params = {
            "period1": str(self._to_unix(start_date)),
            "period2": str(self._to_unix(pd.Timestamp(end_date) + pd.Timedelta(days=1))),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        response = self.session.get(self.BASE_URL.format(symbol=symbol), params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        error = payload.get("chart", {}).get("error")
        result = payload.get("chart", {}).get("result")
        if error or not result:
            raise ValueError(f"Price provider returned no data for {symbol}: {error}")

        chart = result[0]
        timestamps = chart.get("timestamp") or []
        quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
        adjusted = (chart.get("indicators", {}).get("adjclose") or [{}])[0].get(
            "adjclose", quote.get("close", [])
        )
        length = len(timestamps)
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None).normalize(),
                "Open": quote.get("open", [None] * length),
                "High": quote.get("high", [None] * length),
                "Low": quote.get("low", [None] * length),
                "Close": quote.get("close", [None] * length),
                "Adj_Close": adjusted,
                "Volume": quote.get("volume", [None] * length),
            }
        )
        frame["Market"] = market
        frame["Symbol"] = symbol
        frame["Source"] = "Yahoo continuous futures"
        return self._validate(frame)

    @staticmethod
    def _validate(data: pd.DataFrame) -> pd.DataFrame:
        if data.empty or not {"Date", "Close"}.issubset(data.columns):
            return pd.DataFrame()
        clean = data.copy()
        clean["Date"] = pd.to_datetime(clean["Date"], errors="coerce")
        for column in ("Open", "High", "Low", "Close", "Adj_Close", "Volume"):
            if column in clean:
                clean[column] = pd.to_numeric(clean[column], errors="coerce")
        clean = clean.dropna(subset=["Date", "Close"])
        clean = clean.drop_duplicates("Date", keep="last")
        return clean.sort_values("Date").reset_index(drop=True)

    def get_history(
        self,
        market: str,
        start_date: str | pd.Timestamp,
        end_date: str | pd.Timestamp | None = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        end = pd.Timestamp(end_date or datetime.now(timezone.utc).date())
        start = pd.Timestamp(start_date)

        if market in self._memory_cache:
            cached = self._memory_cache[market].copy()
        else:
            cached = self._read_cache(market)

        missing_history = not cached.empty and cached["Date"].min() > start + pd.Timedelta(days=7)
        needs_refresh = (
            force_refresh
            or cached.empty
            or missing_history
            or not self._cache_is_fresh(market)
        )
        if needs_refresh:
            fetch_start = start
            if not cached.empty and not missing_history:
                fetch_start = min(start, cached["Date"].max() - pd.Timedelta(days=14))
            try:
                incoming = self._request(
                    market,
                    fetch_start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                )
                data = self._validate(pd.concat([cached, incoming], ignore_index=True))
                if data.empty:
                    raise ValueError("No valid price rows")
                data.to_csv(self._cache_path(market), index=False)
            except (requests.RequestException, ValueError) as exc:
                if cached.empty:
                    raise RuntimeError(f"Unable to load price data for {market}: {exc}") from exc
                print(f"Price refresh failed for {market}; using cache: {exc}")
                data = cached
        else:
            data = cached
        self._memory_cache[market] = data

        visible = data[(data["Date"] >= start) & (data["Date"] <= end)].copy()
        if not visible.empty:
            visible["Return_1D"] = visible["Close"].pct_change()
            visible["Return_5D"] = visible["Close"].pct_change(5)
            visible["Return_20D"] = visible["Close"].pct_change(20)
        return visible
