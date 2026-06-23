# main.py — APEX Insights API + Dashboard
# Serves market/news data AND the dashboard page from one origin.
# The dashboard is same-origin with the API, so no CORS / no sandbox issues.

import os
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="APEX Insights API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# Where the APEX trading backend lives (the chrono/backtest data).
# Override with an env var if it ever changes.
APEX_BASE = os.environ.get("APEX_BASE", "https://apex-production-b5bc.up.railway.app")

APEX_PAIRS = ["USDJPY", "GBPJPY", "CADJPY", "AUDJPY", "NZDJPY",
              "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]


# ── data endpoints ───────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "APEX Insights API online"}


@app.get("/api/news/latest")
async def news_latest():
    if not NEWSAPI_KEY:
        return {"error": "NEWSAPI_KEY not set"}
    params = {
        "q": "forex OR \"central bank\" OR \"Federal Reserve\" OR \"Bank of Japan\" OR inflation OR \"interest rate\"",
        "language": "en", "sortBy": "publishedAt", "pageSize": 30, "apiKey": NEWSAPI_KEY,
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://newsapi.org/v2/everything", params=params)
        data = r.json()
    arts = [{
        "title": a.get("title"), "source": (a.get("source") or {}).get("name"),
        "publishedAt": a.get("publishedAt"), "url": a.get("url"), "description": a.get("description"),
    } for a in data.get("articles", [])]
    return {"count": len(arts), "articles": arts}


@app.get("/api/forex/trending")
async def forex_trending():
    if not ALPHAVANTAGE_API_KEY:
        return {"error": "ALPHAVANTAGE_API_KEY not set"}
    results = []
    async with httpx.AsyncClient(timeout=20) as c:
        for pair in APEX_PAIRS:
            frm, to = pair[:3], pair[3:]
            try:
                r = await c.get("https://www.alphavantage.co/query", params={
                    "function": "FX_DAILY", "from_symbol": frm, "to_symbol": to,
                    "apikey": ALPHAVANTAGE_API_KEY, "outputsize": "compact",
                })
                series = r.json().get("Time Series FX (Daily)", {})
                closes = [float(v["4. close"]) for v in list(series.values())[:20]]
                if len(closes) >= 10:
                    recent, older = closes[0], closes[9]
                    p = (recent - older) / older * 100
                    results.append({"pair": pair, "direction": "UP" if p > 0 else "DOWN",
                                    "pct_10d": round(p, 2), "trend_strength": round(abs(p), 2)})
            except Exception as e:
                results.append({"pair": pair, "error": str(e)})
    results.sort(key=lambda x: x.get("trend_strength", 0), reverse=True)
    return {"pairs": results}


@app.get("/api/macro/rates")
async def macro_rates():
    if not FRED_API_KEY:
        return {"error": "FRED_API_KEY not set"}
    series = {"fed_funds_rate": "FEDFUNDS", "us_10y": "DGS10", "us_cpi": "CPIAUCSL"}
    out = {}
    async with httpx.AsyncClient(timeout=15) as c:
        for name, sid in series.items():
            r = await c.get("https://api.stlouisfed.org/fred/series/observations", params={
                "series_id": sid, "api_key": FRED_API_KEY, "file_type": "json",
                "sort_order": "desc", "limit": 1,
            })
            obs = r.json().get("observations", [{}])
            out[name] = obs[0] if obs else None
    return out


# ── backtest proxy (server-side; keeps the live trader untouched) ────
@app.get("/api/backtest/{job}")
async def backtest_proxy(job: str):
    """Fetch chrono data from the APEX trading backend server-side and re-serve it.
    The browser never calls the APEX service directly, so the live trader needs no CORS."""
    url = f"{APEX_BASE}/api/chrono/{job}"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse(content={"error": f"could not reach APEX backend: {e}"}, status_code=502)


# ── dashboard page (same origin as the API) ──────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard():
    try:
        with open("dashboard.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>APEX Insights</h1><p>dashboard.html not found in repo.</p>", status_code=200)
