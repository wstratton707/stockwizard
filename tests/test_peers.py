"""Peers are industry comparables, and the comparison is like for like."""
import portfolio_data as P
from analysis import peer_median
from peer_groups import peer_group_for


def test_apple_is_compared_with_platforms_not_chipmakers(monkeypatch):
    monkeypatch.setattr(P, "get_sharpe_rankings", lambda *a, **k: {})
    peers = P.suggest_peers("AAPL", sector="Technology", market_cap=4.9e12)
    assert set(peers) == {"MSFT", "GOOGL", "AMZN", "META"}
    assert not set(peers) & {"NVDA", "AVGO", "MU", "AMD"}


def test_a_share_class_is_not_its_own_peer(monkeypatch):
    monkeypatch.setattr(P, "get_sharpe_rankings", lambda *a, **k: {})
    assert "GOOGL" not in P.suggest_peers("GOOG")


def test_curated_peers_order_by_size_when_sizes_are_known(monkeypatch):
    ranks = {"XOM": {"sector": "Energy", "mcap_est": 480e9},
             "CVX": {"mcap_est": 300e9}, "COP": {"mcap_est": 130e9},
             "EOG": {"mcap_est": 70e9}, "OXY": {"mcap_est": 45e9},
             "DVN": {"mcap_est": 22e9}, "FANG": {"mcap_est": 45e9}}
    monkeypatch.setattr(P, "get_sharpe_rankings", lambda *a, **k: ranks)
    assert P.suggest_peers("XOM", n=2) == ["CVX", "COP"]


def test_uncurated_names_keep_the_sector_rule(monkeypatch):
    monkeypatch.setattr(P, "get_sharpe_rankings", lambda *a, **k: {})
    assert peer_group_for("ZZZZ") is None


def test_peer_median_leaves_out_the_subject_and_blanks():
    rows = [{"ticker": "AAPL", "pe": 38.0}, {"ticker": "MSFT", "pe": 28.0},
            {"ticker": "AMZN", "pe": 20.0}, {"ticker": "META", "pe": None},
            {"ticker": "GOOGL", "pe": 18.0}]
    assert peer_median(rows, skip="AAPL")["pe"] == 20.0
