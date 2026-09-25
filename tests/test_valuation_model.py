"""valuation_model reproduces the report workbook's DCF to the cent.

The expected values are Excel's own calculated results from the reference
workbook (Alphabet, report dated 24-Sep-2026, 'GOOGL_5Y_Analysis UPDATED GOOD
TEMPLATE.xlsx' - not committed; the numbers are copied here). Its inputs go in,
its outputs must come out. If the workbook's formulas change, rebuild these."""
import pytest

import valuation_model as V

GOOGL = V.Inputs(
    price=337.8299865722656, shares=12.23, wacc=0.10632813531354227, tg=0.025,
    g1=0.1715483907713573, margin=0.33110396397123804, years_norm=5, deduct_sbc=False,
    include_nonmkt=True, haircut=0.2, mid_year=True, lr_capex=0.12, da_of_capex=0.85,
    nwc=0.0, capex_guide=180.0, tax=0.17, revenue=445.866, margin0=0.33110396397123804,
    capex0=0.296954690422683, da0=0.05660220783822942, sbc0=0.06312883242947433,
    net_cash=142.31, preferred=18.023, nonmkt=131.461)


def test_wacc_build_matches_the_capm_block():
    w = V.wacc_build(rf10=0.0496, beta_raw=1.2520112419562401, erp=0.05, kd=0.045, tax=0.17,
                     debt=100.164, price=337.8299865722656, shares=12.23)
    assert w["beta_adj"] == pytest.approx(1.1680074946374934, abs=1e-12)
    assert w["cost_of_equity"] == pytest.approx(0.10800037473187468, abs=1e-12)
    assert w["weight_debt"] == pytest.approx(0.02366922220411054, abs=1e-12)
    assert w["wacc"] == pytest.approx(GOOGL.wacc, abs=1e-12)


def test_projection_table_matches_row_by_row():
    rows = V.projection(GOOGL)
    first, last = rows[0], rows[-1]
    assert first["revenue"] == pytest.approx(522.353594799662, abs=1e-9)
    assert first["capex"] == 180                              # year-1 guidance
    assert first["fcf"] == pytest.approx(30.83595632049088, abs=1e-9)
    assert rows[1]["fcf"] == pytest.approx(112.56501705915657, abs=1e-9)
    assert last["growth"] == pytest.approx(0.025, abs=1e-12)
    assert last["revenue"] == pytest.approx(1128.152745375458, abs=1e-8)
    assert last["fcf"] == pytest.approx(360.94696834674494, abs=1e-8)
    assert last["pv"] == pytest.approx(138.21201709034466, abs=1e-8)


def test_bridge_and_fair_value():
    br = V.bridge(GOOGL)
    assert br["pv_explicit"] == pytest.approx(1354.5009812882458, abs=1e-8)
    assert br["terminal_value"] == pytest.approx(4549.110109669615, abs=1e-7)
    assert br["enterprise_value"] == pytest.approx(3010.6022010927254, abs=1e-7)
    assert br["equity_value"] == pytest.approx(3240.058001092725, abs=1e-7)
    assert br["fair_value"] == pytest.approx(264.92706468460545, abs=1e-9)
    assert br["tv_share"] == pytest.approx(0.5500896861110985, abs=1e-12)
    assert abs(br["model_check"]) < 1e-9
    assert V.fair_value(GOOGL) == pytest.approx(264.92706468460557, abs=1e-9)


def test_scenarios_and_probability_weighting():
    sc = V.scenarios(GOOGL)
    assert sc["bear"]["fair_value"] == pytest.approx(159.36011805895032, abs=1e-9)
    assert sc["base"]["fair_value"] == pytest.approx(264.92706468460557, abs=1e-9)
    assert sc["bull"]["fair_value"] == pytest.approx(457.1759285124151, abs=1e-9)
    assert sc["prob_weighted"] == pytest.approx(286.59754398514417, abs=1e-9)
    assert sc["prob_weighted_upside"] == pytest.approx(-0.15165155440149847, abs=1e-12)
    assert sc["bear"]["wacc"] == pytest.approx(0.11632813531354226)
    assert sc["bull"]["tg"] == pytest.approx(0.03)


def test_sensitivity_grids():
    s = V.sensitivities(GOOGL)
    assert s["wacc_tg"]["grid"][0][0] == pytest.approx(273.1769522020529, abs=1e-9)
    assert s["wacc_tg"]["grid"][4][4] == pytest.approx(257.045446235792, abs=1e-9)
    assert s["growth_margin"]["grid"][0][0] == pytest.approx(191.10980700486644, abs=1e-9)
    assert s["growth_margin"]["grid"][4][4] == pytest.approx(363.7473722954289, abs=1e-9)
    assert s["capex_years"]["grid"][0][0] == pytest.approx(277.7820642070273, abs=1e-9)
    assert s["capex_years"]["grid"][4][4] == pytest.approx(251.5960655075426, abs=1e-9)
    assert s["capex_years"]["cols"] == [3, 4, 5, 6, 7]


def test_reverse_dcf_on_the_workbook_grids():
    r = V.reverse(GOOGL)
    assert r["growth"] == pytest.approx(0.2363025335620401, abs=1e-12)
    assert r["cagr"] == pytest.approx(0.1286356469954999, abs=1e-12)
    assert r["year10_revenue"] == pytest.approx(1495.3470067723517, abs=1e-7)
    assert r["margin"] == pytest.approx(0.4475589899969912, abs=1e-12)
    assert r["capex"] is None and r["capex_note"] == "Not reachable via capex alone"


def test_sanity_checks_and_verdict():
    out = V.run(GOOGL)
    ck = out["checks"]
    assert ck["terminal_ev_ebitda"] == pytest.approx(9.310358106979107, abs=1e-9)
    assert ck["terminal_ev_fcf"] == pytest.approx(12.603264492027806, abs=1e-9)
    assert ck["reinvestment_rate"] == pytest.approx(0.06549830067825971, abs=1e-12)
    assert ck["return_on_new_capital"] == pytest.approx(0.381689291800177, abs=1e-12)
    assert ck["integrity"] == "✓ all checks pass"
    assert out["verdict"] == "Priced above model value"
    assert out["verdict_detail"] == "Only the bull case clears today's price"


def test_model_refuses_wacc_at_or_below_terminal_growth():
    assert V.fair_value(GOOGL, wacc=0.02) is None
    assert V.run(V.Inputs(**{**GOOGL.__dict__, "wacc": 0.02}))["checks"]["items"][0][1] == "✗ fix"


def test_excel_match_semantics():
    assert V._match_le([1, 2, 3], 0.5) is None
    assert V._match_le([1, 2, 3], 2) == 1
    assert V._match_le([1, 2, 3], 9) == 2
    assert V._solve_on_grid([0, 1, 2], [10, 20, 30], 25) == pytest.approx(1.5)
    assert V._solve_on_grid([0, 1, 2], [10, 20, 30], 31) is None
