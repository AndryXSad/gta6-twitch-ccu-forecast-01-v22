"""GTA VI launch-day Twitch viewers, hour by hour (v22).

Forecasts the 5/25/50/75/95% quantiles of concurrent viewers for
hours 2..24 of release day from the case file alone (stdlib only,
no network). Design:

  * Three member predictors, each giving a per-hour (log-median,
    log-spread):
      - Y5: tier-filtered per-hour OLS on [lnV1, g12] blended
        (0.5/0.2/0.3) with console-only and all-launch shape views.
      - Z0: same construction with the naive early-access filter.
      - CLONE: whole-library shape views, sample-sd spreads, and a
        weighted-mean blend whose spread is 0.81x the weighted mean
        of the view spreads.
  * The forecast median is the weighted mean of the three members'
    log-medians (0.45 Y5 / 0.35 Z0 / 0.20 clone); the spread is the
    same-weighted mean of their spreads.
  * Per-band asymmetric spread scales (chosen on leave-one-out
    large launches, verified by direct grading): early hours 2-6
    use (lower 0.85, upper 1.05), mid hours 7-14 use (1.05, 0.85),
    late hours 15-24 use (0.95, 0.85). The lower side is wider
    mid/late because the medians run high there (mean log-residual
    about -0.05 on large launches); the early upper side is wider
    because the opening ramp's upper tail matters most then.

Quantiles are lognormal: q_tau = exp(mu + z_tau * sig_side).
"""

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

IN_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = (-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722)
HOURS = tuple(range(2, 25))
# (lower scale, upper scale) per band: early h2-6, mid h7-14, late h15-24.
BANDS = ((0.85, 1.05), (1.05, 0.85), (0.95, 0.85))
BLEND = (("y5", 0.45), ("z0", 0.35), ("clone", 0.20))


# ------------------------------------------------------------- small helpers
def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    m = n // 2
    return s[m] if n % 2 else 0.5 * (s[m - 1] + s[m])


def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def _psd(vals):
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def _ssd(vals):
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _solve(mat, rhs):
    n = len(rhs)
    aug = [list(mat[i]) + [rhs[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-11:
            return None
        aug[col], aug[piv] = aug[piv], aug[col]
        for r in range(n):
            if r == col:
                continue
            f = aug[r][col] / aug[col][col]
            if f == 0.0:
                continue
            for k in range(col, n + 1):
                aug[r][k] -= f * aug[col][k]
    return [aug[i][n] / aug[i][i] for i in range(n)]


def _fit(design, target, ridge=1e-9):
    p = len(design[0])
    gram = [[sum(r[i] * r[j] for r in design)
             + (ridge if i == j else 0.0)
             for j in range(p)] for i in range(p)]
    proj = [sum(r[i] * y for r, y in zip(design, target))
            for i in range(p)]
    return _solve(gram, proj)


def _at(beta, row):
    return sum(b * x for b, x in zip(beta, row))


def _parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


# -------------------------------------------------------------- case loading
def _hourly_map(points):
    out = {}
    for pt in points:
        v = pt.get("viewers")
        if v is None or v <= 0:
            continue
        out[_parse_ts(pt["from"])] = float(v)
    return out


def _launch_feats(vmap, day_start, first):
    start = _parse_ts(day_start)
    prior = [v for t, v in vmap.items()
             if start - timedelta(days=7) <= t < start]
    if not prior:
        return None
    prem = math.log(_mean(prior))
    want = start - timedelta(hours=12)
    best = None
    for t, v in vmap.items():
        gap = abs((t - want).total_seconds())
        if gap <= 3 * 3600 and (best is None or gap < best[0]):
            best = (gap, v)
    g12 = None if best is None else math.log(first) - math.log(best[1])
    return {"lnV1": math.log(first), "lnPREM": prem, "g12": g12}


def _tier_of(note):
    t = (note or "").lower()
    if "early-access-contaminated" in t or "early-access contaminated" in t:
        return "ea"
    if "tier: weak" in t:
        return "weak"
    return "comparable"


def _read_launch(comp):
    vmap = _hourly_map(comp["hourly"])
    start = _parse_ts(comp["day_start"])
    first = vmap.get(start)
    if first is None:
        return None
    curve = {}
    for h in range(1, 25):
        t = start + timedelta(hours=h - 1)
        if t in vmap:
            curve[h] = vmap[t]
    if sum(1 for h in curve if h >= 2) < 20:
        return None
    f = _launch_feats(vmap, comp["day_start"], first)
    if f is None:
        return None
    f["curve"] = curve
    f["console"] = "PC" not in comp.get("platforms", "")
    note = comp.get("unlock", "") or ""
    f["tier"] = _tier_of(note)
    f["space_ea"] = "early access" in note.lower()
    f["hyphen_ea"] = "early-access" in note.lower()
    return f


def _read_case(case):
    vmap = _hourly_map(case["history_hourly"])
    first = max(1.0, float(next(k for k in case["known"]
                                if k["hour"] == 1)["viewers"]))
    tgt = _launch_feats(vmap, case["day_start"], first)
    tgt["curve"] = {}
    lib = []
    for comp in case["comparables"]:
        if "before release" in comp["name"]:
            continue
        parsed = _read_launch(comp)
        if parsed is not None:
            lib.append(parsed)
    med_g12 = _median([f["g12"] for f in lib if f["g12"] is not None])
    if tgt["g12"] is None:
        tgt["g12"] = med_g12
    for f in lib:
        if f["g12"] is None:
            f["g12"] = med_g12
    return tgt, lib


# -------------------------------------------------------------------- views
def _design_row(feats, lo, hi, cols):
    size = _clip(feats["lnV1"], lo, hi + 0.5)
    vals = {"lnV1": size, "lnPREM": feats["lnPREM"],
            "g12": feats["g12"]}
    return [1.0] + [vals[c] for c in cols]


def _window(lib, lnv1, half=2.5):
    near = [f for f in lib if abs(f["lnV1"] - lnv1) <= half]
    return near if len(near) >= 10 else list(lib)


def _ols_view(lib, tgt, hour, cols, mult, sample, after_hour):
    if after_hour:
        pool = [f for f in lib if hour in f["curve"]
                and abs(f["lnV1"] - tgt["lnV1"]) <= 2.5]
        if len(pool) < 10:
            pool = [f for f in lib if hour in f["curve"]]
    else:
        pool = [f for f in _window(lib, tgt["lnV1"])
                if hour in f["curve"]]
    if len(pool) < 8:
        return None
    lo, hi = (min(f["lnV1"] for f in lib),
              max(f["lnV1"] for f in lib))
    design = [_design_row(f, lo, hi, cols) for f in pool]
    ys = [math.log(f["curve"][hour]) for f in pool]
    beta = _fit(design, ys)
    if beta is None:
        return None
    resid = [y - _at(beta, r) for r, y in zip(design, ys)]
    sd = _ssd(resid) if sample else _psd(resid)
    mu = _at(beta, _design_row(tgt, lo, hi, cols)) + _median(resid)
    return mu, sd * mult


def _ea_filter(base, kind):
    if kind == "tier":
        return [f for f in base if f["tier"] != "ea"]
    if kind == "naive":
        return [f for f in base if not f["space_ea"]]
    return [f for f in base if not f["hyphen_ea"]]


def _shape_view(lib, tgt, console_only, feats, mult, sample,
                windowed, ea_kind, fit_hours, min_fits):
    base = _window(lib, tgt["lnV1"]) if windowed else list(lib)
    pool = _ea_filter(base, ea_kind)
    if console_only:
        pool = [f for f in pool if f["console"]]
    if len(pool) < 6:
        pool = _ea_filter(base, ea_kind)
    if len(pool) < 6:
        return None
    template = {}
    for h in HOURS:
        vals = [math.log(f["curve"][h] / f["curve"][1]) for f in pool
                if h in f["curve"] and 1 in f["curve"]]
        template[h] = _median(vals) if vals else 0.0
    fits = []
    for f in pool:
        xs = [template[h] for h in HOURS if h in f["curve"]]
        ys = [math.log(f["curve"][h] / f["curve"][1]) for h in HOURS
              if h in f["curve"]]
        if len(xs) < fit_hours:
            continue
        mx, my = _mean(xs), _mean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
                 if den else 0.0)
        fits.append((f, my - slope * mx, slope))
    if len(fits) < min_fits:
        return None
    lo, hi = (min(f["lnV1"] for f in lib),
              max(f["lnV1"] for f in lib))
    design = [_design_row(f, lo, hi, feats) for f, _, _ in fits]
    beta_c = _fit(design, [c for _, c, _ in fits])
    beta_s = _fit(design, [s for _, _, s in fits])
    if beta_c is None or beta_s is None:
        return None
    xt = _design_row(tgt, lo, hi, feats)
    c_star = _at(beta_c, xt)
    s_star = _clip(_at(beta_s, xt), 0.0, 2.0)
    out = {}
    for h in HOURS:
        res = [math.log(f["curve"][h] / f["curve"][1])
               - (c + s * template[h])
               for f, c, s in fits if h in f["curve"]]
        sd = _ssd(res) if sample else _psd(res)
        out[h] = (tgt["lnV1"] + c_star + s_star * template[h], sd * mult)
    return out


# ------------------------------------------------------------------ members
def _v4_member(lib, tgt, ea_kind, feats):
    con = _shape_view(lib, tgt, True, feats, 1.0, False, True,
                      ea_kind, 10, 6)
    all_ = _shape_view(lib, tgt, False, feats, 1.0, False, True,
                       ea_kind, 10, 6)
    out = {}
    for h in HOURS:
        views = []
        ov = _ols_view(lib, tgt, h, ("lnV1", "lnPREM", "g12"),
                       0.8, False, False)
        if ov is not None:
            views.append((0.5, ov))
        else:
            views.append((0.5, (tgt["lnV1"], 0.5)))
        if con is not None and h in con:
            views.append((0.2, con[h]))
        if all_ is not None and h in all_:
            views.append((0.3, all_[h]))
        tot = sum(w for w, _ in views)
        mu = sum(w * v[0] for w, v in views) / tot
        sig = math.sqrt(max(0.02, sum(w * v[1] ** 2 for w, v in views)
                            / tot))
        out[h] = (mu, sig)
    return out


def _clone_member(lib, tgt):
    feats = ("lnV1", "lnPREM", "g12")
    con = _shape_view(lib, tgt, True, feats, 1.0, True, False,
                      "hyphen", 20, 5)
    all_ = _shape_view(lib, tgt, False, feats, 1.0, True, False,
                       "hyphen", 20, 5)
    out = {}
    for h in HOURS:
        views = []
        ov = _ols_view(lib, tgt, h, feats, 0.8, True, True)
        if ov is not None:
            views.append((0.6, ov))
        if con is not None and h in con:
            views.append((0.2, con[h]))
        if all_ is not None and h in all_:
            views.append((0.2, all_[h]))
        if not views:
            out[h] = (tgt["lnV1"], 0.5)
            continue
        tot = sum(w for w, _ in views)
        mu = sum(w * v[0] for w, v in views) / tot
        sig = 0.81 * sum(w * v[1] for w, v in views) / tot
        out[h] = (mu, sig)
    return out


def _member(kind, lib, tgt):
    if kind == "y5":
        return _v4_member(lib, tgt, "tier", ("lnV1", "g12"))
    if kind == "z0":
        return _v4_member(lib, tgt, "naive", ("lnV1", "g12"))
    return _clone_member(lib, tgt)


# ------------------------------------------------------------------- output
def _band(hour):
    return 0 if hour <= 6 else 1 if hour <= 14 else 2


def forecast(case):
    tgt, lib = _read_case(case)
    preds = {kind: _member(kind, lib, tgt) for kind, _ in BLEND}
    rows = []
    for h in HOURS:
        mu = sum(w * preds[k][h][0] for k, w in BLEND)
        sig = sum(w * preds[k][h][1] for k, w in BLEND)
        lo_s, hi_s = BANDS[_band(h)]
        q = []
        for z in Z:
            s = lo_s if z < 0 else hi_s if z > 0 else 0.0
            q.append(min(1e9, max(1.0, math.exp(mu + z * sig * s))))
        rows.append({"hour": h, "q": q})
    return {"hours": rows}


def _check_output(out):
    """Fail loudly rather than emit an invalid forecast file.

    RULES requires exactly 23 rows (hours 2..24), each five finite
    non-decreasing quantiles in (0, 1e9]. The construction guarantees
    this (sig >= 0, z ascending, exp bounded), so a violation here
    means a bug upstream, not a bad case -- and an invalid case is
    scored as if every quantile were the hour-1 print.
    """
    rows = out["hours"]
    assert len(rows) == 23, f"expected 23 rows, got {len(rows)}"
    for row, h in zip(rows, HOURS):
        assert row["hour"] == h, f"row hour {row['hour']} != {h}"
        q = row["q"]
        assert len(q) == 5, f"hour {h}: expected 5 quantiles"
        prev = 0.0
        for v in q:
            assert math.isfinite(v) and 0.0 < v <= 1e9, \
                f"hour {h}: quantile {v} out of range"
            assert v >= prev, f"hour {h}: quantiles not non-decreasing"
            prev = v


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(IN_DIR.glob("*.in")):
        case = json.loads(path.read_text())
        out = forecast(case)
        _check_output(out)
        (OUT_DIR / (path.stem + ".out")).write_text(json.dumps(out))


if __name__ == "__main__":
    main()
