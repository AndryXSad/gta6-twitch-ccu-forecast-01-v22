# DESIGN.md — gta6-twitch-ccu-forecast-01 v22

## The bar
- v2 (verified, direct-graded): practice **0.128297** (p00 0.1215 / p01
  0.1296 / p02 0.0907 / p03 0.1713), LOO-large 0.2113, LOO-mega 0.2137.
- House baseline: 0.387077. Live bar at 17 Nov ~17:27 WIB: #1 0.0975,
  top-10 ≈ 0.115, top-20 ≈ 0.122, ~3,127 entries. Prizes pay top 20.
- Owner's rule: keep improving until a candidate hits practice ≤ 0.100,
  then submit it. This version does not reach 0.100 — it is the first
  version in 22 rounds to beat v2 on BOTH practice and LOO.

## What v22 is
A 3-way weighted-mean blend (45% Y5 / 35% Z0 / 20% clone) with
**per-band asymmetric spread scales**:

| band | hours | lower (q05/q25) | upper (q75/q95) |
| ---- | ----- | --------------- | --------------- |
| early | 2–6 | 0.85 | 1.05 |
| mid | 7–14 | 1.05 | 0.85 |
| late | 15–24 | 0.95 | 0.85 |

Direct-graded practice **0.127258** (p00 0.1223 / p01 0.1311 / p02 0.0900 /
p03 0.1656); LOO-large **0.210621**, LOO-mega **0.212838**. A second,
central-pattern cell — early (0.95, 1.05), mid (1.05, 0.85), late
(0.95, 0.85) — direct-graded **0.127313** with LOO 0.210521 / 0.212713,
confirming the pattern is not a one-cell fluke.

## Why this shape
Twenty-one rounds measured the same frontier: every global spread scale
trades practice against LOO in the same ratio (v14), because the two
error regimes want opposite spread shapes — practice's worst misses are
early-hour troughs below the median (punished less by a narrow lower
side), while LOO launches' worst misses are mid-day drift above the
median (v16: medians biased HIGH on LOO-large, mean log-residual ≈
−0.05, so the lower side needs width). Global asymmetry (v21) measured
the corner to ±0.0002 and still missed both gates. Splitting the
asymmetry by band is the first construction that moves the corner: the
mid/late bands take the LOO-favoured shape (wide lower, narrow upper)
while the early band takes the practice-favoured upper width.

## The honest caveat (read this before trusting the number)
The 6 band parameters were chosen on LOO-large over a 729-cell grid, so
LOO-large is **not a clean holdout** for the chosen cell — its margin
(−0.0007) is optimistically biased by selection. The practice margin
(−0.0010) is the cleaner evidence, and it was confirmed by direct
grading of the actual program (0.127258 vs the harness's 0.127259).
31 of 729 cells pass both gates and they share one coherent structure
(mid/late (1.05, 0.85) in 25 of 31; early hi = 1.05 in most), which is
more than a single lucky cell — but the final is a new launch in a new
regime (lnV1 ≈ 14+, beyond the library max 13.68), and practice is only
4 launches. Treat this as the best verified fallback, not a guarantee.

## Members (all in log space)
- **Y5**: v4-family member — tier EA filter, per-hour OLS on
  [lnV1, g12], shape views (console-only + all-launch, windowed,
  population sd), mix 0.5/0.2/0.3, sqrt-variance blend floored at
  sqrt(0.02). Best median-only loss ever seen (0.1975).
- **Z0**: same construction, naive 'early access' (space) filter.
- **Clone**: v9 black-box reconstruction of v2's spread rule — whole
  library shape pools, ≥20-hour fits, 3-feature shape regressions,
  sample sd everywhere, OLS pool counted after the hour filter,
  weighted-mean blend with spread = 0.81 × weighted mean of spreads,
  mix 0.6/0.2/0.2.

Features: lnV1 = log hour-1 viewers; lnPREM = log MEAN viewers in the
7 days before release day; g12 = lnV1 − log viewers 12h before release
(nearest point within 3h, library-median fallback). Size fed to the
regressions is clamped to the library's lnV1 range + 0.5 headroom.

## What was tried and dropped (rounds v3–v22)
- Blend weights: practice is flat to 0.0005 across the whole 2-way
  weight grid (v20) — weights are almost a non-lever.
- Global/per-band symmetric spread scales, per-hour LOO-spread,
  large-only LOO-spread, empirical quantile offsets, roughness-indexed
  and inverse-roughness spreads, per-case feature-indexed scales
  (v14–v19): all re-measure the same frontier.
- Bias corrections (per-hour, global, empirical) (v3/v7/v17): the
  −0.05 bias is a tail, not a location; shifts make everything worse.
- Channels view, pre-release roughness, unlock-aligned shapes, streams
  and categories_hourly hybrids (v12/v18/v19): no signal, or actively
  harmful (unlock field pins only 8/37 launches).
- Robust/quantile/local/GP/quadratic/peak-aligned medians (v5/v6/v10/
  v11): help small launches, hurt large/mega — GTA VI's regime.
- Asymmetric spreads on the cross-pairings (v22 idea 1): no cell
  clears both gates (closest: Y5 median + clone spread lo=1.02/hi=0.96,
  practice +0.000310, LOO −0.000060).
- Per-hour median-of-views member (v22 idea 3): a worse median than Y5
  (median-only practice 0.2037 vs 0.1975); blended 50/50 with Y5 at
  scale 0.8 it reaches practice 0.126802 but LOO 0.223081 — dead.

## Verification
- Harness calibration: house 0.387077; v2's actual main.py reproduces
  0.128297 exactly; clone 0.129250; Y5 0.130685; ensemble 0.127632;
  half-blend 0.126261; 3-way x0.92 0.128311 — all exact.
- This program direct-graded on the 4 practice cases with the exact
  RULES scorer: **0.127258** (harness 0.127259; the 1e-6 gap is the
  g12-median fallback convention). Runtime ~0.16 s/case, deterministic.
