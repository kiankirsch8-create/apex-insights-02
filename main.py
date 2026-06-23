# main.py — APEX Insights API
# Separate service. Serves market/news data to the dashboard.
# Does NOT touch the live trader.

import os
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="APEX Insights API")

# Allow the dashboard (and you) to call these endpoints from a browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten later to your dashboard URL
    allow_methods=["GET"],
    allow_headers=["*"],
)

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# Forex pairs APEX cares about most
APEX_PAIRS = ["USDJPY", "GBPJPY", "CADJPY", "AUDJPY", "NZDJPY",
              "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]


@app.get("/")
def root():
    return {"status": "APEX Insights API online"}


@app.get("/api/news/latest")
async def news_latest():
    """Recent forex/macro-relevant headlines from NewsAPI."""
    if not NEWSAPI_KEY:
        return {"error": "NEWSAPI_KEY not set"}
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "forex OR \"central bank\" OR \"Federal Reserve\" OR \"Bank of Japan\" OR inflation OR interest rates",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": NEWSAPI_KEY,
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, params=params)
        data = r.json()
    articles = [
        {
            "title": a.get("title"),
            "source": a.get("source", {}).get("name"),
            "publishedAt": a.get("publishedAt"),
            "url": a.get("url"),
            "description": a.get("description"),
        }
        for a in data.get("articles", [])
    ]
    return {"count": len(articles), "articles": articles}


@app.get("/api/forex/trending")
async def forex_trending():
    """Trend strength per APEX pair using AlphaVantage daily data.
    Returns each pair's recent direction and a simple trend score."""
    if not ALPHAVANTAGE_API_KEY:
        return {"error": "ALPHAVANTAGE_API_KEY not set"}

    results = []
    async with httpx.AsyncClient(timeout=20) as c:
        for pair in APEX_PAIRS:
            frm, to = pair[:3], pair[3:]
            params = {
                "function": "FX_DAILY",
                "from_symbol": frm,
                "to_symbol": to,
                "apikey": ALPHAVANTAGE_API_KEY,
                "outputsize": "compact",
            }
            try:
                r = await c.get("https://www.alphavantage.co/query", params=params)
                series = r.json().get("Time Series FX (Daily)", {})
                closes = [float(v["4. close"]) for v in list(series.values())[:20]]
                if len(closes) >= 10:
                    recent, older = closes[0], closes[9]
                    pct = (recent - older) / older * 100
                    results.append({
                        "pair": pair,
                        "direction": "UP" if pct > 0 else "DOWN",
                        "pct_10d": round(pct, 2),
                        "trend_strength": round(abs(pct), 2),
                    })
            except Exception as e:
                results.append({"pair": pair, "error": str(e)})

    # NOTE: AlphaVantage free tier is 25 calls/day, 5/min — this uses 9 calls per request.
    results.sort(key=lambda x: x.get("trend_strength", 0), reverse=True)
    return {"pairs": results}


@app.get("/api/forex/quote/{pair}")
async def forex_quote(pair: str):
    """Live-ish quote for one pair via Finnhub."""
    if not FINNHUB_API_KEY:
        return {"error": "FINNHUB_API_KEY not set"}
    symbol = f"OANDA:{pair[:3]}_{pair[3:]}"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
        )
        return {"pair": pair, "data": r.json()}


@app.get("/api/macro/rates")
async def macro_rates():
    """Key macro series from FRED — Fed funds rate, etc."""
    if not FRED_API_KEY:
        return {"error": "FRED_API_KEY not set"}
    series = {
        "fed_funds_rate": "FEDFUNDS",
        "us_10y": "DGS10",
        "us_cpi": "CPIAUCSL",
    }
    out = {}
    async with httpx.AsyncClient(timeout=15) as c:
        for name, sid in series.items():
            r = await c.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": sid,
                    "api_key": FRED_API_KEY,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                },
            )
            obs = r.json().get("observations", [{}])
            out[name] = obs[0] if obs else None
    return out
