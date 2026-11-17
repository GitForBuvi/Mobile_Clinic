"""Our own Python function tools (the challenge requires at least one).

The planner tool never reads race or ethnicity. The audit tool is the only place in the
system that does, and it only runs inside the auditor agent.

Every number we hand back carries a `scale` label so the agents can say where it came from:
  tract     -> CDC PLACES / CDC SVI, modeled or survey estimates per census tract
  facility  -> straight-line km from tract centre to a HRSA/CMS care-site point
  grid      -> EPA PM2.5, 12 km model grid (NOT tract-level, never rank tracts on it)
  region    -> CDC NSSP, multi-county Health Service Area
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional

import pandas as pd
from google.adk.tools import ToolContext

from . import config

# ---------------------------------------------------------------------------- helpers


@lru_cache(maxsize=1)
def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=config.PROJECT or None)


def _query(sql: str) -> pd.DataFrame:
    return _bq().query(sql).to_dataframe()


def _km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km."""
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _r(x, nd=1):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


# Inputs the planner is allowed to see. No race, no ethnicity, no RPL_THEMES.
CHRONIC = ["pct_diabetes", "pct_high_bp", "pct_copd", "pct_asthma"]
CANT_REACH = ["pct_no_vehicle", "pct_mobility_difficulty", "pct_age_65_plus"]

WEIGHTS = {
    # priority -> (uninsured, chronic burden, can't reach care, km to safety-net clinic)
    "balanced": (0.30, 0.25, 0.20, 0.25),
    "uninsured": (0.50, 0.15, 0.15, 0.20),
    "access": (0.20, 0.15, 0.30, 0.35),
}


def _load_tracts() -> pd.DataFrame:
    sql = f"""
    SELECT b.geo_id, b.total_pop, b.latitude, b.longitude,
           b.pct_uninsured, b.pct_checkup,
           {", ".join("b." + c for c in CHRONIC + CANT_REACH)},
           a.km_to_any_care, a.km_to_safety_net, a.extra_km_if_uninsured
    FROM {config.table("burden_tracts")} b
    LEFT JOIN {config.table("tract_access")} a USING (geo_id)
    WHERE b.latitude IS NOT NULL AND b.longitude IS NOT NULL
    """
    return _query(sql)


def score_tracts(df: pd.DataFrame, priority: str = "balanced") -> pd.DataFrame:
    """Pure function: percentile-rank every input inside the county, combine, sort.

    Kept separate from the tool so it can be unit-tested without BigQuery.
    """
    w_unins, w_chronic, w_reach, w_dist = WEIGHTS.get(priority, WEIGHTS["balanced"])
    df = df.copy()
    pr = lambda col: df[col].rank(pct=True)  # NaN stays NaN; we report it, never guess it
    df["pr_uninsured"] = pr("pct_uninsured")
    df["pr_chronic"] = pd.concat([pr(c) for c in CHRONIC], axis=1).mean(axis=1, skipna=True)
    df["pr_cant_reach"] = pd.concat([pr(c) for c in CANT_REACH], axis=1).mean(axis=1, skipna=True)
    df["pr_distance"] = pr("km_to_safety_net")
    parts = df[["pr_uninsured", "pr_chronic", "pr_cant_reach", "pr_distance"]]
    weights = pd.Series([w_unins, w_chronic, w_reach, w_dist], index=parts.columns)
    # Re-normalise weights per row over the parts that exist, so a missing input lowers
    # confidence (reported) rather than silently scoring the tract as zero need.
    df["need_score"] = (parts * weights).sum(axis=1) / parts.notna().mul(weights).sum(axis=1)
    df["missing_inputs"] = df[["pct_uninsured", *CHRONIC, *CANT_REACH, "km_to_safety_net"]] \
        .isna().apply(lambda r: [c for c, m in r.items() if m], axis=1)
    return df.sort_values("need_score", ascending=False)


def pick_stops(df: pd.DataFrame, n_stops: int, min_spacing_km: float,
               exclude: set[str], min_pop: int) -> list[dict]:
    """Greedy: walk down the ranking, skip tracts too close to a stop already chosen."""
    chosen: list[dict] = []
    for _, r in df.iterrows():
        if r.geo_id in exclude or (r.total_pop or 0) < min_pop:
            continue
        if any(_km(r.latitude, r.longitude, c["latitude"], c["longitude"]) < min_spacing_km
               for c in chosen):
            continue
        chosen.append({
            "rank": len(chosen) + 1,
            "geo_id": r.geo_id,
            "need_score": _r(r.need_score, 3),
            "total_pop": int(r.total_pop) if pd.notna(r.total_pop) else None,
            "latitude": _r(r.latitude, 5),
            "longitude": _r(r.longitude, 5),
            "evidence": {
                "pct_uninsured_18_64": {"value": _r(r.pct_uninsured), "scale": "tract",
                                        "source": "CDC PLACES (modeled estimate)"},
                "pct_diabetes": {"value": _r(r.pct_diabetes), "scale": "tract",
                                 "source": "CDC PLACES (modeled estimate)"},
                "pct_high_bp": {"value": _r(r.pct_high_bp), "scale": "tract",
                                "source": "CDC PLACES (modeled estimate)"},
                "pct_no_vehicle": {"value": _r(r.pct_no_vehicle), "scale": "tract",
                                   "source": "CDC/ATSDR SVI (ACS survey)"},
                "pct_age_65_plus": {"value": _r(r.pct_age_65_plus), "scale": "tract",
                                    "source": "CDC/ATSDR SVI (ACS survey)"},
                "km_to_safety_net": {"value": _r(r.km_to_safety_net, 2), "scale": "facility",
                                     "source": "straight line to nearest FQHC/health center"},
                "extra_km_if_uninsured": {"value": _r(r.extra_km_if_uninsured, 2),
                                          "scale": "facility",
                                          "source": "safety-net km minus any-care km"},
            },
            "missing_inputs": list(r.missing_inputs),
        })
        if len(chosen) == n_stops:
            break
    return chosen


# ----------------------------------------------------------------------- planner tool


def rank_tracts_for_van(
    tool_context: ToolContext,
    n_stops: int = 4,
    min_spacing_km: float = 0.0,
    exclude_geo_ids: Optional[list[str]] = None,
    priority: str = "balanced",
    min_population: int = 500,
) -> dict:
    """Rank the county's census tracts by unmet need and pick mobile-clinic stops.

    Need combines, as within-county percentile ranks: uninsured adults, chronic
    screenable disease (diabetes, high blood pressure, COPD, asthma), people who
    cannot easily travel (no vehicle, mobility difficulty, 65+), and straight-line
    km to the nearest safety-net clinic. Race and ethnicity are NOT inputs.

    Args:
      n_stops: how many Saturday stops to choose (default 4).
      min_spacing_km: minimum km between chosen stops (0 = pure need ranking).
      exclude_geo_ids: tract IDs to skip, e.g. ones the auditor rejected.
      priority: "balanced", "uninsured" or "access" weighting.
      min_population: skip tracts with fewer residents than this.

    Returns:
      The chosen stops with every number labelled by scale, plus the method used.
      Also writes them to session state key `ranked_tracts`.
    """
    df = score_tracts(_load_tracts(), priority)
    stops = pick_stops(df, n_stops, min_spacing_km, set(exclude_geo_ids or []), min_population)
    result = {
        "county": config.COUNTY_NAME,
        "tracts_considered": int(len(df)),
        "method": {
            "priority": priority,
            "weights_uninsured_chronic_cantreach_distance": WEIGHTS.get(priority,
                                                                         WEIGHTS["balanced"]),
            "min_spacing_km": min_spacing_km,
            "excluded": exclude_geo_ids or [],
            "inputs_never_used": ["race", "ethnicity", "SVI theme 3", "RPL_THEMES",
                                  "PM2.5 (12 km grid, cannot separate tracts)"],
        },
        "stops": stops,
    }
    tool_context.state["ranked_tracts"] = result  # <- read by the equity auditor
    return result


# ----------------------------------------------------------------------- auditor tools

# Public ACS 5-year tract table (US Government work). Tried in order; 2020 tracts first
# because PLACES and SVI use 2020 boundaries.
ACS_TABLES = [
    "bigquery-public-data.census_bureau_acs.censustract_2020_5yr",
    "bigquery-public-data.census_bureau_acs.censustract_2019_5yr",
]
RACE_COLS = ["white_pop", "black_pop", "hispanic_pop", "asian_pop", "amerindian_pop"]


def _acs_for(geo_ids: list[str]) -> tuple[pd.DataFrame, str]:
    ids = ",".join(f"'{g}'" for g in geo_ids)
    last_err = None
    for t in ACS_TABLES:
        try:
            df = _query(f"SELECT geo_id, total_pop AS acs_pop, {', '.join(RACE_COLS)} "
                        f"FROM `{t}` WHERE geo_id IN ({ids})")
            return df, t
        except Exception as e:  # table or column missing -> try the next one
            last_err = e
    raise RuntimeError(f"No ACS race table worked: {last_err}")


def audit_plan_equity(tool_context: ToolContext, max_cluster_km: float = 3.0) -> dict:
    """Audit the planner's stops. Reads session state `ranked_tracts`.

    Compares who lives in the recommended tracts with the whole county, using race and
    ethnicity columns the planner was not allowed to see, and checks the plan for
    problems the planner can fix (stops clustered together, missing inputs, tiny tracts).

    Args:
      max_cluster_km: stops closer than this are flagged as duplicate coverage.

    Returns:
      Findings (demographic comparison, need comparison, fixable problems).
      Also writes them to session state key `audit_findings`.
    """
    plan = tool_context.state.get("ranked_tracts")
    if not plan or not plan.get("stops"):
        return {"error": "No plan in state['ranked_tracts'] yet. The planner must run first."}
    stops = plan["stops"]
    chosen = [s["geo_id"] for s in stops]

    county = _query(f"SELECT geo_id, total_pop, pct_uninsured, km_to_safety_net "
                    f"FROM {config.table('tract_access')}")
    acs, acs_table = _acs_for(county.geo_id.tolist())
    m = county.merge(acs, on="geo_id", how="left")
    joined = int(m["acs_pop"].notna().sum())

    def shares(frame):
        tot = frame["acs_pop"].sum()
        return {c.replace("_pop", ""): _r(100 * frame[c].sum() / tot) if tot else None
                for c in RACE_COLS}

    in_plan = m[m.geo_id.isin(chosen)]
    plan_sh, county_sh = shares(in_plan), shares(m)
    gaps = {k: _r((plan_sh[k] or 0) - (county_sh[k] or 0)) for k in plan_sh}

    # Problems the PLANNER can fix without ever seeing race.
    fixable = []
    for i, a in enumerate(stops):
        for b in stops[i + 1:]:
            d = _km(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
            if d < max_cluster_km:
                fixable.append({"issue": "clustered_stops", "geo_ids": [a["geo_id"], b["geo_id"]],
                                "km_apart": _r(d, 2),
                                "fix": f"re-plan with min_spacing_km >= {max_cluster_km}"})
    for s in stops:
        if s["missing_inputs"]:
            fixable.append({"issue": "missing_inputs", "geo_id": s["geo_id"],
                            "fields": s["missing_inputs"],
                            "fix": "exclude or justify; the score was partly guessed"})

    findings = {
        "acs_table": acs_table,
        "acs_join": f"{joined} of {len(m)} county tracts matched ACS",
        "population_share_pct": {"recommended_tracts": plan_sh, "county": county_sh,
                                 "difference_pts": gaps},
        "need_check": {
            "recommended_mean_uninsured": _r(in_plan.pct_uninsured.mean()),
            "county_mean_uninsured": _r(m.pct_uninsured.mean()),
            "recommended_mean_km_to_safety_net": _r(in_plan.km_to_safety_net.mean(), 2),
            "county_mean_km_to_safety_net": _r(m.km_to_safety_net.mean(), 2),
        },
        "fixable_problems": fixable,
        "scale_note": "Race/ethnicity are ACS tract estimates (scale: tract). Four tracts is "
                      "a small sample; report differences as descriptive, not significant.",
    }
    tool_context.state["audit_findings"] = findings  # <- read by reviewer + final writer
    return findings


def request_revision(tool_context: ToolContext, reason: str,
                     min_spacing_km: float = 3.0,
                     exclude_geo_ids: Optional[list[str]] = None) -> dict:
    """Reject the current plan and tell the planner what to change.

    Only ask for changes the planner can make WITHOUT using race (spacing, exclusions).

    Args:
      reason: one or two sentences the planner will read.
      min_spacing_km: spacing the next plan must use.
      exclude_geo_ids: tracts the next plan must skip.
    """
    req = {"reason": reason, "min_spacing_km": min_spacing_km,
           "exclude_geo_ids": exclude_geo_ids or []}
    tool_context.state["revision_request"] = req  # <- read by the planner next loop
    history = list(tool_context.state.get("rejections", []))
    history.append(req)
    tool_context.state["rejections"] = history
    return {"status": "revision requested", **req}


def approve_plan(tool_context: ToolContext, summary: str) -> dict:
    """Approve the current plan and end the plan/audit loop.

    Args:
      summary: two or three sentences on what the audit found, including null results.
    """
    tool_context.state["audit_verdict"] = summary
    tool_context.state["revision_request"] = None
    tool_context.actions.escalate = True  # exits the LoopAgent
    return {"status": "approved"}
