"""
allocators.py — Portfolio weighting schemes that do NOT require expected returns.

Why this module exists
----------------------
`portfolio_analysis.optimise_portfolio` maximises a Sharpe ratio, so it needs a
vector of expected returns. That is the single noisiest input in portfolio
construction: errors in mu are far more damaging than errors in the covariance
matrix, and mu is the harder of the two to estimate. Michaud's name for
mean-variance optimisation — "error maximiser" — describes the mechanism: the
optimiser reads a noisy high estimate as an opportunity and levers into it.

Every allocator here sidesteps that by never forming an expected-return vector
at all. They differ only in what they do with the covariance matrix:

    equal_weight  ignores it entirely
    gmv           minimises portfolio variance
    erc           equalises each holding's contribution to portfolio risk
    hrp           clusters the correlation matrix and splits risk down the tree,
                  never inverting the covariance matrix

All four take a daily-returns DataFrame and return {ticker: weight} summing to
1.0, so they are drop-in interchangeable with optimise_portfolio's output.

Constraints are not a compromise here — they are an estimator. Jagannathan & Ma
(2003) showed a no-short-sale constraint is mathematically equivalent to
shrinking the large elements of the sample covariance matrix and then optimising
unconstrained, which is why the long-only bounds below are load-bearing rather
than cosmetic.
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from portfolio_analysis import shrunk_covariance


# Same defaults the live builder uses, so a horse race compares allocation
# schemes rather than accidentally comparing constraint sets.
DEFAULT_MAX_WEIGHT        = 0.30
DEFAULT_MAX_SECTOR_WEIGHT = 0.40


# ── shared constraint plumbing ────────────────────────────────────────────────

def _bounds(n, max_weight):
    """Long-only bounds, feasibility-clamped.

    If n * max_weight < 1 the budget constraint cannot be met and SLSQP fails
    silently into whatever the fallback is, so the cap is widened rather than
    allowed to produce a broken portfolio.
    """
    max_weight = max(0.05, min(max_weight, 1.0))
    if n * max_weight < 1.0:
        max_weight = 1.0 / n + 1e-3
    min_w = min(0.02, 0.80 / n)
    return [(min_w, max_weight)] * n, min_w, max_weight


def _sector_constraints(cols, sector_map, max_sector_weight, n, min_w, max_weight):
    """Sector caps, skipping any that would make the problem infeasible.

    Mirrors optimise_portfolio: "Market", "Commodities", "User" and "Unknown"
    are not capped. Unknown is the absence of a label, not a sector — treating
    it as one bucket makes the cap unsatisfiable whenever most names carry it.
    """
    if not sector_map:
        return []
    from collections import defaultdict
    idx = defaultdict(list)
    for i, t in enumerate(cols):
        s = sector_map.get(t, "Unknown")
        if s not in ("Market", "Commodities", "User", "Unknown"):
            idx[s].append(i)

    capped = {s: m for s, m in idx.items() if len(m) > 1}
    if not capped:
        return []

    # Collective feasibility, which per-sector checks miss. Each capped sector
    # can absorb at most `cap`; everything uncapped can absorb at most
    # max_weight each. If those do not sum to 1.0 the budget constraint is
    # unsatisfiable no matter how the weights are arranged — SLSQP then hits its
    # iteration limit and the caller falls back to equal weights, which looks
    # like a deliberate portfolio but is a failure.
    #
    # Concretely: 12 names in 2 sectors, each capped at 40%, tops out at 80%.
    # Both sectors pass every per-sector test. The problem only exists globally.
    n_free   = n - sum(len(m) for m in capped.values())
    capacity = sum(min(max_sector_weight, len(m) * max_weight)
                   for m in capped.values()) + n_free * max_weight
    eff_cap  = max_sector_weight
    if capacity < 1.0:
        # Relax the cap to the tightest value that still admits a solution,
        # rather than abandoning sector control altogether.
        deficit = 1.0 - n_free * max_weight
        eff_cap = min(1.0, max(max_sector_weight, deficit / len(capped) + 1e-3))

    out = []
    for _sector, members in capped.items():
        # The sector's own floor must still fit under the (possibly relaxed) cap.
        if len(members) * min_w > eff_cap:
            continue
        out.append({"type": "ineq",
                    "fun": lambda w, m=members, c=eff_cap: c - sum(w[i] for i in m)})
    return out


def _normalise(w, cols):
    w = np.clip(np.asarray(w, dtype=float), 0.0, None)
    total = w.sum()
    if total <= 0:
        w = np.ones(len(cols)) / len(cols)
        total = 1.0
    return {c: float(w[i] / total) for i, c in enumerate(cols)}


# ── 1/N ───────────────────────────────────────────────────────────────────────

def equal_weight(returns_df, **_kwargs):
    """The benchmark that 14 models across 7 datasets failed to consistently beat
    (DeMiguel, Garlappi & Uppal 2009). Accepts and ignores the keyword arguments
    the other allocators take so callers can treat all four identically.
    """
    cols = list(returns_df.columns)
    return {c: 1.0 / len(cols) for c in cols}


# ── Global minimum variance ───────────────────────────────────────────────────

def gmv(returns_df, sector_map=None, max_weight=DEFAULT_MAX_WEIGHT,
        max_sector_weight=DEFAULT_MAX_SECTOR_WEIGHT, cov=None, **_kwargs):
    """Constrained global minimum-variance portfolio.

    The only point on the mean-variance frontier whose weights are completely
    independent of expected returns — which is exactly why it tends to beat the
    tangency portfolio out of sample. It needs Sigma and nothing else.
    """
    cols = list(returns_df.columns)
    n    = len(cols)
    if n == 1:
        return {cols[0]: 1.0}

    _cov = shrunk_covariance(returns_df) if cov is None else cov
    bounds, min_w, max_weight = _bounds(n, max_weight)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    cons += _sector_constraints(cols, sector_map, max_sector_weight, n, min_w, max_weight)

    init = np.ones(n) / n
    res  = minimize(lambda w: float(np.sqrt(w @ _cov @ w)), init,
                    method="SLSQP", bounds=bounds, constraints=cons)
    return _normalise(res.x if res.success else init, cols)


# ── Equal risk contribution ───────────────────────────────────────────────────

def erc(returns_df, sector_map=None, max_weight=DEFAULT_MAX_WEIGHT,
        max_sector_weight=DEFAULT_MAX_SECTOR_WEIGHT, cov=None, **_kwargs):
    """Equal risk contribution, a.k.a. risk parity (Maillard, Roncalli &
    Teiletche 2010).

    Each holding contributes the same share of total portfolio risk, where the
    contribution of asset i is w_i * (Sigma w)_i / sigma_p. That is a stronger
    statement than equal weight: a volatile name gets a smaller position so its
    risk share matches everyone else's.

    Sits between GMV and 1/N by construction — the published result is that its
    volatility lands between the two, which makes it the usual compromise pick
    when GMV concentrates too hard into the calmest few names.
    """
    cols = list(returns_df.columns)
    n    = len(cols)
    if n == 1:
        return {cols[0]: 1.0}

    _cov = shrunk_covariance(returns_df) if cov is None else cov

    def _risk_budget_error(w):
        # Dispersion of FRACTIONAL risk contributions around the 1/n target.
        #
        # The fractional form matters. Contributions in raw units sum to the
        # portfolio's volatility (~0.09), so each is ~0.008 and their squared
        # dispersion is ~1e-6 — flat enough that SLSQP's finite-difference
        # gradients read as numerical noise and it stops almost where it
        # started, returning something indistinguishable from equal weight.
        # Normalising to fractions that sum to 1 and scaling to O(1) gives the
        # solver a surface it can actually descend.
        port_var = float(w @ _cov @ w)
        if port_var <= 0:
            return 1e6
        frac = w * (_cov @ w) / port_var          # sums to 1 by construction
        return float(np.sum((frac - 1.0 / n) ** 2) * 1e3)

    bounds, min_w, max_weight = _bounds(n, max_weight)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    cons += _sector_constraints(cols, sector_map, max_sector_weight, n, min_w, max_weight)

    # Inverse-volatility start. Plain 1/N is a poor seed here: it sits in a flat
    # region of the objective and SLSQP often stops before it has moved much.
    vol  = np.sqrt(np.diag(_cov))
    init = (1.0 / vol) / np.sum(1.0 / vol) if np.all(vol > 0) else np.ones(n) / n
    init = np.clip(init, min_w, max_weight)
    init = init / init.sum()

    res = minimize(_risk_budget_error, init, method="SLSQP",
                   bounds=bounds, constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-12})
    return _normalise(res.x if res.success else init, cols)


# ── Hierarchical risk parity ──────────────────────────────────────────────────

def _quasi_diagonal(link):
    """Seriation: read the dendrogram back into a leaf ordering that puts
    correlated assets adjacent, so the covariance matrix becomes quasi-diagonal.
    """
    link = link.astype(int)
    order = pd.Series([link[-1, 0], link[-1, 1]])
    n_items = link[-1, 3]
    while order.max() >= n_items:
        order.index = range(0, order.shape[0] * 2, 2)     # make room
        df0 = order[order >= n_items]
        i, j = df0.index, df0.values - n_items
        order[i] = link[j, 0]
        order = pd.concat([order, pd.Series(link[j, 1], index=i + 1)])
        order = order.sort_index()
        order.index = range(order.shape[0])
    return order.tolist()


def _cluster_var(cov, items):
    sub = cov[np.ix_(items, items)]
    ivp = 1.0 / np.diag(sub)
    ivp /= ivp.sum()
    return float(ivp @ sub @ ivp)


def hrp(returns_df, sector_map=None, max_weight=DEFAULT_MAX_WEIGHT,
        max_sector_weight=DEFAULT_MAX_SECTOR_WEIGHT, cov=None, **_kwargs):
    """Hierarchical Risk Parity (Lopez de Prado).

    Three steps: cluster assets on a correlation distance, reorder the
    covariance matrix so correlated names sit adjacent, then split capital down
    the tree by inverse cluster variance.

    Its structural advantage is that it never inverts the covariance matrix.
    Matrix inversion is where an ill-conditioned Sigma does its damage, and
    every other risk-based method here depends on it. HRP has been shown to
    deliver lower out-of-sample variance than constraint-based minimum variance
    even though minimum variance is optimising for exactly that.

    `sector_map` and `max_sector_weight` are accepted for interface parity and
    deliberately unused: HRP derives its own grouping from the correlation
    structure, and imposing a second, sector-based grouping on top of it would
    fight the algorithm rather than constrain it.
    """
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform

    cols = list(returns_df.columns)
    n    = len(cols)
    if n == 1:
        return {cols[0]: 1.0}
    if n == 2:
        return _cap(_normalise(np.array([0.5, 0.5]), cols), max_weight)

    _cov  = shrunk_covariance(returns_df) if cov is None else cov
    corr  = returns_df.corr().values
    corr  = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, 1.0)

    # Lopez de Prado's correlation distance, then the Euclidean distance between
    # those distance vectors — the second transform is what makes the linkage
    # respond to whole correlation profiles rather than pairwise values alone.
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
    np.fill_diagonal(dist, 0.0)
    link = linkage(squareform(dist, checks=False), method="single")

    order = _quasi_diagonal(link)
    w     = np.ones(n)
    clusters = [order]
    while clusters:
        # Bisect every cluster in the current generation.
        #
        # The halves must be expressed as (start, end) pairs, not as a start
        # plus a fixed length. An odd-length cluster splits into len//2 and
        # len-len//2, so slicing the second half as c[len//2 : len//2+len//2]
        # silently drops its last element. Anything dropped never gets its
        # weight scaled down and keeps the initial 1.0, so it survives
        # normalisation as an enormous position — which is how a risk-parity
        # method ends up with the highest volatility in the field.
        clusters = [c[j:k]
                    for c in clusters
                    for j, k in ((0, len(c) // 2), (len(c) // 2, len(c)))
                    if len(c) > 1]
        for k in range(0, len(clusters), 2):
            left, right = clusters[k], clusters[k + 1]
            v_left, v_right = _cluster_var(_cov, left), _cluster_var(_cov, right)
            # Inverse-variance split: the calmer half of the split gets more.
            alpha = 1.0 - v_left / (v_left + v_right) if (v_left + v_right) > 0 else 0.5
            w[left]  *= alpha
            w[right] *= (1.0 - alpha)

    return _cap(_normalise(w, cols), max_weight)


def _cap(weights, max_weight):
    """Apply a position cap to weights that were produced without one.

    HRP has no constraint mechanism, so the cap is applied afterwards: clip the
    offenders, redistribute the excess across the uncapped names in proportion
    to what they already hold, and repeat until nothing breaches. Converges in a
    handful of passes; the iteration limit is a guard, not an expectation.
    """
    if max_weight >= 1.0:
        return weights
    keys = list(weights)
    w    = np.array([weights[k] for k in keys], dtype=float)
    if len(w) * max_weight < 1.0:                 # cap infeasible — leave alone
        return weights
    for _ in range(50):
        over = w > max_weight + 1e-12
        if not over.any():
            break
        excess    = float((w[over] - max_weight).sum())
        w[over]   = max_weight
        room      = ~over
        base      = float(w[room].sum())
        if base <= 0:
            w[room] += excess / max(room.sum(), 1)
        else:
            w[room] += excess * (w[room] / base)
    return {k: float(v / w.sum()) for k, v in zip(keys, w)}


# Registry so callers can iterate methods by name.
ALLOCATORS = {
    "1/N": equal_weight,
    "GMV": gmv,
    "ERC": erc,
    "HRP": hrp,
}
