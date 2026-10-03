import json

import numpy as np
import pandas as pd
import pytest

from radar import backtest, fundamentals, regime, report, scoring, track
from radar import indicators as ind
from radar.data import Prices, clean_market, load_universe


def days(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2018-01-01", periods=n)


def trending(n: int, daily: float, start: float = 100.0) -> pd.Series:
    return pd.Series(start * (1 + daily) ** np.arange(n), index=days(n))


def wavy(n: int, daily: float) -> pd.Series:
    """A trend with a regular wiggle, so that RSI is not pinned at 0 or 100."""
    return trending(n, daily) * (1 + 0.02 * np.sin(np.arange(n) / 3))


def as_prices(close: pd.DataFrame) -> Prices:
    return Prices(close=close, high=close * 1.01, low=close * 0.99)


# --- indicators -------------------------------------------------------------------------------------

def test_rsi_is_100_when_price_only_rises_and_0_when_it_only_falls():
    assert ind.rsi(trending(50, 0.01), 14).iloc[-1] == pytest.approx(100)
    assert ind.rsi(trending(50, -0.01), 14).iloc[-1] == pytest.approx(0)


def test_momentum_12_1_skips_the_most_recent_month():
    close = trending(300, 0.001)
    close.iloc[-21:] = close.iloc[-22] * 5  # a spike inside the skipped month must not count
    expected = close.iloc[-22] / close.iloc[-253] - 1
    assert ind.momentum(close, 252, 21).iloc[-1] == pytest.approx(expected)


def test_max_drawdown():
    equity = pd.Series([1.0, 2.0, 1.0, 1.5, 3.0])
    assert ind.max_drawdown(equity) == pytest.approx(-0.5)


# --- backtest engine --------------------------------------------------------------------------------

def test_weights_only_earn_the_next_days_return():
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0, 121.0]}, index=days(4))
    weights = pd.DataFrame({"A": [0.0, 1.0, 1.0, 1.0]}, index=days(4))  # decided on the close of day 1
    returns = backtest.portfolio_returns(close, weights, cost=0.0)
    assert returns.tolist() == pytest.approx([0.0, 0.0, 0.10, 0.0])


def test_a_signal_that_sees_the_same_day_cannot_profit_from_it():
    rng = np.random.default_rng(1)
    close = pd.DataFrame({"A": 100 * np.cumprod(1 + rng.normal(0, 0.02, 500))}, index=days(500))
    peeking = (close.pct_change() > 0).astype(float)  # "long on up days", known only after the close
    returns = backtest.portfolio_returns(close, peeking, cost=0.0)
    same_day_edge = (peeking * close.pct_change()).sum().iloc[0]
    assert returns.sum() < same_day_edge / 5


def test_costs_are_charged_on_turnover():
    close = pd.DataFrame({"A": [100.0] * 4}, index=days(4))
    weights = pd.DataFrame({"A": [1.0, 1.0, 0.0, 0.0]}, index=days(4))
    returns = backtest.portfolio_returns(close, weights, cost=0.001)
    assert returns.sum() == pytest.approx(-0.002)  # one buy and one sell


def test_holdings_drift_between_rebalances():
    close = pd.DataFrame({"A": [100.0, 200.0, 200.0], "B": [100.0, 100.0, 200.0]}, index=days(3))
    weights = pd.DataFrame(0.5, index=days(3), columns=["A", "B"])
    returns = backtest.portfolio_returns(close, weights, cost=0.0)
    assert (1 + returns).prod() == pytest.approx(2.0)  # bought once and held: 50 + 50 -> 100 + 100


def test_monthly_rebalance_trades_back_to_target_and_pays_for_it():
    index = pd.bdate_range("2024-01-01", "2024-03-29")
    close = pd.DataFrame({"A": np.linspace(100, 200, len(index)), "B": 100.0}, index=index)
    weights = pd.DataFrame(0.5, index=index, columns=["A", "B"])
    free = backtest.portfolio_returns(close, weights, cost=0.0, monthly=True)
    paid = backtest.portfolio_returns(close, weights, cost=0.01, monthly=True)
    month_ends = index.to_series().groupby([index.year, index.month]).max()
    assert set(index[(free - paid) > 1e-12]) == set(month_ends[:-1])  # March may still be running


def test_drawdown_counts_a_loss_on_the_first_day():
    returns = pd.Series([-0.5] + [0.0] * 251, index=days(252))
    assert backtest.stats(returns, 252)["max_drawdown"] == pytest.approx(-0.5)


def test_empty_model_variable_falls_back_to_the_default(monkeypatch):
    import anthropic

    from radar import ai

    seen = {}

    class Messages:
        def create(self, **kwargs):
            seen.update(kwargs)
            raise anthropic.APIConnectionError(request=None)

    class Client:
        beta = type("Beta", (), {"messages": Messages()})()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("RADAR_MODEL", "")
    monkeypatch.setattr(anthropic, "Anthropic", Client)
    assert ai.commentary({"regime": {"state": "risk_on"}, "picks": {}}, None) is None
    assert seen["model"] == ai.DEFAULT_MODEL


def test_clean_market_measures_staleness_against_the_given_date():
    close = pd.DataFrame({"OLD": trending(400, 0.001)})
    later = close.index.max() + pd.Timedelta(days=30)
    cleaned, dropped = clean_market(as_prices(close), now=later)
    assert dropped == ["OLD"] and cleaned.close.empty


def test_trend_strategy_leaves_a_falling_market():
    close = pd.concat([trending(400, 0.001), trending(400, -0.002, start=149)]).reset_index(drop=True)
    close.index = days(800)
    weights = backtest.trend_sma(close)
    assert weights.iloc[350, 0] == 1.0
    assert weights.iloc[-1, 0] == 0.0


def test_momentum_rotation_picks_winners_and_stays_in_cash_without_uptrend():
    close = pd.DataFrame({"WIN": trending(400, 0.002), "OK": trending(400, 0.0005),
                          "LOSE": trending(400, -0.001)})
    weights = backtest.momentum_rotation(close, top_n=1)
    assert weights.iloc[-1].idxmax() == "WIN"
    assert weights.iloc[-1].sum() == pytest.approx(1.0)

    falling = pd.DataFrame({"A": trending(400, -0.001), "B": trending(400, -0.002)})
    assert backtest.momentum_rotation(falling, top_n=1).iloc[-1].sum() == 0.0


def test_momentum_rotation_never_exceeds_full_investment_on_ties():
    close = pd.DataFrame({name: trending(400, 0.001) for name in "ABCD"})
    weights = backtest.momentum_rotation(close, top_n=3)
    assert (weights.iloc[-1] > 0).sum() == 3
    assert weights.iloc[-1].sum() == pytest.approx(1.0)


def test_dual_momentum_moves_to_bonds_when_equities_fall():
    close = pd.DataFrame({"SPY": trending(400, -0.001), "EFA": trending(400, -0.002),
                          "IEF": trending(400, 0.0001)})
    weights = backtest.dual_momentum(close, ["SPY", "EFA"], "IEF")
    assert weights.iloc[-1].to_dict() == {"SPY": 0.0, "EFA": 0.0, "IEF": 1.0}


def test_stats_of_a_steady_riser():
    returns = pd.Series(0.001, index=days(504))
    s = backtest.stats(returns, 252)
    assert s["cagr"] == pytest.approx(1.001**252 - 1)
    assert s["max_drawdown"] == 0.0
    assert s["calmar"] is None


# --- scoring ----------------------------------------------------------------------------------------

def market() -> Prices:
    return as_prices(pd.DataFrame({
        "STRONG": wavy(400, 0.002), "FLAT": wavy(400, 0.0002),
        "WEAK": wavy(400, -0.001), "CRASH": wavy(400, -0.003),
    }))


def test_strong_uptrend_is_a_buy_and_downtrend_is_avoided():
    scored = scoring.score_market(market(), 252)
    assert scored.index[0] == "STRONG"
    assert scored.loc["STRONG", "long_signal"] == scoring.BUY
    assert scored.loc["CRASH", "long_signal"] == scoring.AVOID
    assert any("Abwärtstrend" in r for r in scored.loc["CRASH", "risks"])
    assert scored.loc["STRONG", "stop"] < scored.loc["STRONG", "price"]


def test_best_of_a_falling_market_is_not_a_buy():
    falling = as_prices(pd.DataFrame({"A": trending(400, -0.0005), "B": trending(400, -0.002),
                                      "C": trending(400, -0.003)}))
    scored = scoring.score_market(falling, 252)
    assert scoring.BUY not in set(scored["long_signal"]) | set(scored["short_signal"])


def test_risk_off_suspends_short_term_buys():
    assert scoring.score_market(market(), 252).loc["FLAT", "short_signal"] == scoring.BUY
    assert scoring.score_market(market(), 252, risk_off=True).loc["FLAT", "short_signal"] == scoring.WATCH


def test_overbought_asset_is_not_a_short_term_buy():
    scored = scoring.score_market(market(), 252)
    assert scored.loc["STRONG", "rsi14"] > 75
    assert scored.loc["STRONG", "short_signal"] == scoring.WATCH
    assert any("Überkauft" in r for r in scored.loc["STRONG", "risks"])


def test_quality_tilts_the_long_score():
    base = scoring.score_market(market(), 252).loc["FLAT", "long_score"]
    quality = pd.Series({"FLAT": 1.0, "STRONG": 0.0})
    tilted = scoring.score_market(market(), 252, quality).loc["FLAT", "long_score"]
    assert tilted > base


# --- regime, fundamentals, data ---------------------------------------------------------------------

def test_regime_is_risk_off_when_all_indices_are_below_their_average():
    close = pd.DataFrame({"^GSPC": trending(300, -0.001), "^GDAXI": trending(300, -0.001),
                          "^VIX": trending(300, 0.0)})
    assert regime.market_regime(close, {})["state"] == "risk_off"
    close = pd.DataFrame({"^GSPC": trending(300, 0.001), "^GDAXI": trending(300, 0.001)})
    assert regime.market_regime(close, {})["state"] == "risk_on"


def test_quality_rank_prefers_profitable_cheap_companies():
    data = {
        "GOOD": {"returnOnEquity": 0.4, "profitMargins": 0.3, "revenueGrowth": 0.2, "forwardPE": 12},
        "BAD": {"returnOnEquity": 0.02, "profitMargins": 0.01, "revenueGrowth": -0.1, "forwardPE": 60},
        "THIN": {"returnOnEquity": 0.1},
    }
    rank = fundamentals.quality_rank(data, ["GOOD", "BAD", "THIN", "MISSING"])
    assert rank["GOOD"] > rank["BAD"]
    assert pd.isna(rank["THIN"]) and pd.isna(rank["MISSING"])


def test_clean_market_drops_short_and_stale_series():
    close = pd.DataFrame({"OK": trending(400, 0.001), "SHORT": trending(400, 0.001), "STALE": trending(400, 0.001)})
    close.loc[close.index[:300], "SHORT"] = np.nan
    close.loc[close.index[-30:], "STALE"] = np.nan
    cleaned, dropped = clean_market(as_prices(close))
    assert list(cleaned.close.columns) == ["OK"]
    assert sorted(dropped) == ["SHORT", "STALE"]


def test_universe_has_no_duplicate_tickers():
    universe = load_universe()
    tickers = [t for m in ("us", "eu", "etf", "crypto") for t in universe[m]["tickers"]]
    assert len(tickers) == len(set(tickers))


# --- track record, changes, context ------------------------------------------------------------------

def asset(ticker: str, long: str, short: str = "Meiden", price: float = 100.0) -> dict:
    return {"ticker": ticker, "name": ticker, "price": price, "long_signal": long, "short_signal": short}


def test_signal_changes_lists_moves_and_puts_buys_first():
    before = [asset("A", "Halten"), asset("B", "Meiden"), asset("C", "Kaufen")]
    after = [asset("A", "Beobachten"), asset("B", "Kaufen"), asset("C", "Kaufen"), asset("NEW", "Kaufen")]
    changes = track.signal_changes(before, after, "2026-01-02T10:00:00Z")
    moves = [(c["ticker"], c["from"], c["to"]) for c in changes]
    assert moves == [("B", "Meiden", "Kaufen"), ("A", "Halten", "Beobachten")]


def test_track_record_opens_on_buy_and_closes_with_the_realised_return(tmp_path):
    file = tmp_path / "track.json"
    track.update_track(file, [asset("A", "Kaufen", price=100.0)], "2026-01-01T10:00:00Z")
    running = track.update_track(file, [asset("A", "Kaufen", price=110.0)], "2026-01-02T10:00:00Z")
    assert running["open"]["long"]["A"]["return"] == pytest.approx(0.10)
    assert running["stats"]["closed"] == 0

    done = track.update_track(file, [asset("A", "Halten", price=120.0)], "2026-01-03T10:00:00Z")
    assert done["open"]["long"] == {}
    assert done["closed"][0]["return"] == pytest.approx(0.20)
    assert done["closed"][0]["since"] == "2026-01-01T10:00:00Z"
    assert done["stats"] == {"closed": 1, "hit_rate": 1.0, "avg_return": pytest.approx(0.20), "open": 0,
                             "open_avg_return": None}


def test_track_record_survives_a_stock_split(tmp_path):
    file = tmp_path / "track.json"
    before = pd.DataFrame({"A": [100.0, 100.0]}, index=days(2))
    track.update_track(file, [asset("A", "Kaufen", price=100.0)], "2026-01-01T10:00:00Z", before)
    # 2-for-1 split: Yahoo halves the adjusted history, the live quote halves too.
    after = pd.DataFrame({"A": [50.0, 50.0, 55.0]}, index=days(3))
    record = track.update_track(file, [asset("A", "Kaufen", price=55.0)], "2026-01-02T10:00:00Z", after)
    assert record["open"]["long"]["A"]["entry"] == pytest.approx(50.0)
    assert record["open"]["long"]["A"]["return"] == pytest.approx(0.10)


def test_track_record_keeps_a_signal_open_when_the_price_is_missing(tmp_path):
    file = tmp_path / "track.json"
    track.update_track(file, [asset("A", "Kaufen")], "2026-01-01T10:00:00Z")
    later = track.update_track(file, [], "2026-01-02T10:00:00Z")
    assert "A" in later["open"]["long"] and later["closed"] == []


def test_atom_feed_only_carries_buy_related_changes_and_escapes_names():
    changes = [
        {"at": "2026-01-02T10:00:00Z", "ticker": "T", "name": "AT&T", "horizon": "long", "from": "Halten",
         "to": "Kaufen", "price": 20.0},
        {"at": "2026-01-02T10:00:00Z", "ticker": "X", "name": "X", "horizon": "long", "from": "Halten",
         "to": "Meiden", "price": 1.0},
    ]
    feed = track.atom_feed(changes, "https://example.org/", "2026-01-02T10:00:00Z")
    assert "AT&amp;T" in feed and feed.count("<entry>") == 1
    import xml.dom.minidom
    xml.dom.minidom.parseString(feed)


def test_fundamentals_context_reports_days_to_the_next_earnings_date():
    now = 1_800_000_000
    data = {"A": {"earningsTimestampStart": now + 3 * 86400 + 60, "dividendYield": 1.8, "trailingPE": -4.0},
            "B": {"earningsTimestamp": now - 10 * 86400}}
    context = fundamentals.context(data, now=now)
    assert context["A"]["earnings_in_days"] == 3
    assert context["A"]["dividend_yield"] == pytest.approx(0.018)
    assert context["A"]["pe"] is None
    assert context["B"]["earnings_in_days"] is None


def test_upcoming_earnings_become_a_risk_and_breadth_counts_uptrends():
    scored = scoring.score_market(market(), 252)
    meta = {"STRONG": {"earnings_in_days": 1, "sector": "Tech"}}
    rows = {r["ticker"]: r for r in report.asset_rows(scored, "us", {}, meta)}
    assert any("Quartalszahlen morgen" in r for r in rows["STRONG"]["risks"])
    assert rows["STRONG"]["sector"] == "Tech" and rows["FLAT"]["sector"] is None
    assert rows["STRONG"]["target"] > rows["STRONG"]["price"] > rows["STRONG"]["stop"]
    assert report.breadth(list(rows.values()), {"us": "US"}) == {"us": {"above": 2, "total": 4}}


def test_chart_series_thins_the_last_year_and_keeps_the_latest_session():
    from radar.cli import chart_series

    close = pd.DataFrame({"A": trending(600, 0.001)})
    chart = chart_series(close)
    assert len(chart["dates"]) == 130
    assert chart["dates"][-1] == close.index[-1].strftime("%Y-%m-%d")
    assert chart["series"]["A"]["c"][-1] == pytest.approx(close["A"].iloc[-1], rel=1e-4)
    assert chart["series"]["A"]["s200"][-1] is not None


def test_backtest_summary_reports_the_current_allocation():
    close = pd.DataFrame({name: wavy(700, 0.001 * (i + 1)) for i, name in
                          enumerate(["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "VNQ", "DBC"])})
    empty = pd.DataFrame()
    results = {b["key"]: b for b in backtest.run_all({"etf": close, "us": empty, "eu": empty, "crypto": empty})}
    allocation = results["etf_rotation"]["allocation"]
    assert len(allocation) == 3 and sum(allocation.values()) == pytest.approx(1.0, abs=0.01)
    assert results["bh_spy"]["allocation"] == {"SPY": 1.0}


def test_notifications_are_skipped_without_configuration(monkeypatch):
    from radar import notify

    for name in ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NTFY_TOPIC"):
        monkeypatch.delenv(name, raising=False)
    assert notify.send("t", "x", "https://example.org") == []


# --- news, traffic light, sectors, badges -------------------------------------------------------------

def test_news_keeps_only_known_fields_https_links_and_known_tickers():
    from radar import news

    raw = {
        "at": "2026-10-03T21:26:00Z",
        "evil": "<script>",
        "market": [{"text": "Ruhiger Handel.", "url": "https://example.org/a"}],
        "items": [
            {"ticker": "AAA", "pro": [{"text": "  Gute Zahlen.  ", "url": "https://example.org/b", "x": 1}],
             "con": [{"text": "Abstufung.", "url": "javascript:alert(1)"}, {"text": ""}, "kaputt"],
             "dates": [{"text": "T" * 1000}]},
            {"ticker": "UNKNOWN", "pro": [{"text": "x"}]},
            "kaputt",
        ],
    }
    clean = news.sanitize(raw, {"AAA"})
    assert set(clean) == {"at", "market", "items"}
    assert [i["ticker"] for i in clean["items"]] == ["AAA"]
    item = clean["items"][0]
    assert item["pro"] == [{"text": "Gute Zahlen.", "url": "https://example.org/b"}]
    assert item["con"] == [{"text": "Abstufung.", "url": None}]
    assert len(item["dates"][0]["text"]) == news.MAX_TEXT


def test_news_rejects_unusable_documents(tmp_path):
    from radar import news

    assert news.sanitize({"at": "gestern", "items": []}) is None
    assert news.sanitize({"at": "2026-10-03T21:26:00Z", "items": []}) is None
    assert news.sanitize(["kein", "dict"]) is None
    target = tmp_path / "news.json"
    (tmp_path / "raw.json").write_text("{kaputt", encoding="utf-8")
    assert news.import_file(tmp_path / "raw.json", target) is False
    assert news.import_file(tmp_path / "fehlt.json", target) is False
    assert not target.exists()


def test_ampel_is_green_in_a_calm_broad_uptrend_and_red_in_a_stressed_downtrend():
    indices = {"^GSPC": {"uptrend": True}, "^GDAXI": {"uptrend": True}, "BTC-USD": {"uptrend": False}}
    up = {"indices": indices, "vix": 13}
    good = regime.ampel(up, {"us": {"above": 9, "total": 10}, "eu": {"above": 8, "total": 10}})
    assert good["label"] == "Grün" and good["score"] > 85
    down = {"indices": {"^GSPC": {"uptrend": False}, "^GDAXI": {"uptrend": False}}, "vix": 40}
    bad = regime.ampel(down, {"us": {"above": 1, "total": 10}})
    assert bad["label"] == "Rot" and bad["score"] < 10


def test_sectors_and_badges_summarise_the_payload():
    scored = scoring.score_market(market(), 252)
    meta = {"STRONG": {"sector": "Tech"}, "FLAT": {"sector": "Tech"}, "CRASH": {"sector": "Energy"}}
    rows = report.asset_rows(scored, "us", {"STRONG": "Strong AG"}, meta)
    by_sector = {s["sector"]: s for s in report.sectors(rows)}
    assert by_sector["Tech"]["count"] == 2 and by_sector["Tech"]["uptrend"] == 2
    assert by_sector["Energy"]["uptrend"] == 0 and by_sector["Energy"]["buys"] == 0
    assert report.sectors(rows)[0]["sector"] == "Tech"

    badges = report.badges(payload())
    assert badges["long"]["message"] == "Strong AG"
    assert badges["assets"] == {"schemaVersion": 1, "label": "Werte", "message": "4", "color": "D4F77A",
                                "labelColor": "121113"}


# --- report -----------------------------------------------------------------------------------------

def payload() -> dict:
    scored = scoring.score_market(market(), 252)
    assets = report.asset_rows(scored, "us", {"STRONG": "Strong AG"})
    backtests = [
        {"key": "a", "name": "A", "survivorship_bias": False, "out_of_sample":
            {"cagr": 0.1, "sharpe": 0.9, "max_drawdown": -0.1, "calmar": 1.0}},
        {"key": "b", "name": "B", "survivorship_bias": True, "out_of_sample":
            {"cagr": 0.3, "sharpe": 1.5, "max_drawdown": -0.1, "calmar": 3.0}},
    ]
    state = {"state": "risk_on", "label": "Risk-on", "text": "ok", "vix": 15.0, "indices": {}}
    return report.build_payload(pd.Timestamp("2026-01-02 10:00"), state, assets, {"us": "US-Aktien"}, backtests, [])


def test_payload_is_valid_json_without_nan():
    text = json.dumps(payload(), allow_nan=False)
    assert "STRONG" in text


def test_core_strategy_ignores_survivorship_biased_backtests():
    assert payload()["core_strategy"] == "a"


def test_picks_only_contain_buy_signals():
    p = payload()
    assert p["picks"]["overall"]["long"][0] == "STRONG"
    assert not {"WEAK", "CRASH"} & set(p["picks"]["overall"]["long"] + p["picks"]["overall"]["short"])


def test_markdown_names_the_pick_and_reports_changes():
    moved = [{"name": "Strong AG", "ticker": "STRONG", "horizon": "long", "from": "Halten", "to": "Kaufen"}]
    text = report.markdown(payload(), {"short": ["OLD"], "long": ["STRONG"]}, moved)
    assert "langfristig: Halten → Kaufen" in text
    assert "Strong AG" in text
    assert "raus `OLD`" in text
    assert "Keine Anlageberatung" in text


def test_history_is_appended_and_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "HISTORY_LIMIT", 2)
    file = tmp_path / "history.json"
    for _ in range(3):
        history = report.update_history(file, payload())
    assert len(history) == 2
    assert history[-1]["long"]["ticker"] == "STRONG"
