"""Challenge 5: Mobile Clinic Broker, multi-agent ADK system.

Flow for every request (root_agent is a SequentialAgent):

  1. context_scouts  (ParallelAgent, MCP Toolbox -> BigQuery)
       disease_scout   -> state["disease_context"]   scale: region
       shortage_scout  -> state["shortage_context"]  scale: area/facility
       air_scout       -> state["air_context"]       scale: 12 km grid
  2. plan_audit_loop (LoopAgent, max 3 rounds)
       planner         -> state["ranked_tracts"]     (our tool; no race inputs)
       equity_auditor  -> state["audit_findings"]    (own context; sees race; can reject)
  3. safety_reviewer -> state["reviewed_plan"]       hedge, cite scale, refuse medical
  4. communicator    -> state["neighborhood_message"] warm, plain, bilingual
  5. plan_writer     -> final Clinic Plan from all state keys

Conflicting instructions on purpose: safety_reviewer (hedge everything, cite every
number) vs communicator (no numbers, no hedging, 6th-grade reading level).
"""
import os

from google.adk.agents import LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

from . import config
from .tools import approve_plan, audit_plan_equity, rank_tracts_for_van, request_revision

MODEL = config.MODEL
DS = f"{config.PROJECT}.{config.DATASET}"

# ------------------------------------------------------------- Google-managed MCP server
# MCP Toolbox for Databases, prebuilt BigQuery tools. We consume it; we don't author it.
_args = ["--prebuilt", "bigquery", "--stdio"]
if config.TOOLBOX_CMD == "npx":
    _args = ["-y", "@toolbox-sdk/server"] + _args


def bigquery_mcp() -> McpToolset:
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=config.TOOLBOX_CMD,
                args=_args,
                env={**os.environ, "BIGQUERY_PROJECT": config.PROJECT},
            ),
            timeout=60,
        ),
        tool_filter=["execute_sql", "get_table_info", "list_table_ids"],
    )


SCOUT_RULES = f"""
Use the execute_sql tool against dataset `{DS}`. Run at most 3 queries.
Reply in under 120 words. Start every number with its scale in brackets, e.g. [region].
If a table is empty or a column is all NULL, say "NO SIGNAL" plainly: no signal is not
the same as no problem. Never talk about individual patients.
"""

# ---------------------------------------------------------------- 1. parallel scouts
disease_scout = LlmAgent(
    name="disease_scout",
    model=MODEL,
    description="Respiratory illness trend for the Health Service Area.",
    instruction=f"""You report whether COVID, flu or RSV emergency visits are rising.
Table `{DS}.disease_weekly` (one row per week, column week_end, percent_visits_* columns).
Compare the latest 4 weeks with the 4 before. This data is REGIONAL (multi-county Health
Service Area), never tract-level. {SCOUT_RULES}""",
    tools=[bigquery_mcp()],
    output_key="disease_context",
)

shortage_scout = LlmAgent(
    name="shortage_scout",
    model=MODEL,
    description="HRSA shortage designations.",
    instruction=f"""You report HRSA shortage designations for the county.
`{DS}.shortage_areas` = areas designated underserved (may be EMPTY: say so).
`{DS}.shortage_facilities` = clinics that ALREADY serve underserved people (where care IS).
Never UNION or add these two tables together. {SCOUT_RULES}""",
    tools=[bigquery_mcp()],
    output_key="shortage_context",
)

air_scout = LlmAgent(
    name="air_scout",
    model=MODEL,
    description="Air quality context at the scale it actually works.",
    instruction=f"""You report air quality from `{DS}.exposure_tracts`
(mean_pm, max_pm, days_over_12/20/25/35). These values come from a 12 km model grid,
so they CANNOT tell one neighbourhood from another. Report only the county-wide picture
and the worst days, and say explicitly that the planner must not rank tracts on it.
{SCOUT_RULES}""",
    tools=[bigquery_mcp()],
    output_key="air_context",
)

context_scouts = ParallelAgent(
    name="context_scouts",
    description="Gather region/grid/area context concurrently.",
    sub_agents=[disease_scout, shortage_scout, air_scout],
)

# ------------------------------------------------------------- 2. plan <-> audit loop
planner = LlmAgent(
    name="planner",
    model=MODEL,
    description="Chooses mobile-clinic stops from tract-level need.",
    instruction="""You are the siting planner for a county mobile health clinic van.
Choose WHERE the van parks on four Saturdays. You do not decide who to treat.

Call rank_tracts_for_van exactly once.
- First round: use the user's wishes (number of stops, priority) or the defaults.
- Revision request from the auditor (empty on the first round): {revision_request?}
  If it is not empty/None, you MUST pass its min_spacing_km and exclude_geo_ids.

Then explain the ranking in at most 6 bullet points: tract id, the numbers that drove it,
and each number's scale (tract / facility). You never see race or ethnicity, and you must
not guess at them.""",
    tools=[rank_tracts_for_van],
    output_key="planner_notes",
)

equity_auditor = LlmAgent(
    name="equity_auditor",
    model=MODEL,
    description="Independent equity audit of the planner's output.",
    # Does NOT see the planner's conversation, only state. Independence by construction.
    include_contents="none",
    instruction="""You are an independent equity auditor for a county health department.
You did not make this plan and you do not defend it.

Plan to audit (from state): {ranked_tracts?}

1. Call audit_plan_equity.
2. If it lists fixable_problems (clustered stops, missing inputs), call request_revision
   with a short reason, min_spacing_km and any exclude_geo_ids. Do this even on the
   first plan if the problems are real.
3. Otherwise call approve_plan with a summary that states, in plain numbers, how the
   recommended tracts' population compares with the county by race/ethnicity, and
   whether need (uninsured, km to safety-net) is higher than the county average.
   Report a "no meaningful difference" result honestly; it is still a finding.

Never ask the planner to select or avoid tracts because of race or ethnicity: race is
an audit of the output, not an input.""",
    tools=[audit_plan_equity, request_revision, approve_plan],
    output_key="auditor_notes",
)

plan_audit_loop = LoopAgent(
    name="plan_audit_loop",
    description="Planner proposes, auditor approves or sends it back.",
    sub_agents=[planner, equity_auditor],
    max_iterations=3,
)

# ------------------------------------------- 3 + 4. two agents that genuinely conflict
safety_reviewer = LlmAgent(
    name="safety_reviewer",
    model=MODEL,
    description="Rigor and clinical-safety review. Hedges, cites, refuses if unsure.",
    instruction="""You are a cautious public-health epidemiologist reviewing a plan.
Rules: hedge every estimate; attach the scale (tract / facility / grid / region) and
source to EVERY number; flag any sentence that sounds like medical advice and remove it;
list what the data cannot tell us (named gaps, NO SIGNAL layers, modeled estimates,
straight-line distance is not travel time). If unsure, say you are unsure.

Plan: {ranked_tracts?}
Audit: {audit_findings?}
Auditor verdict: {audit_verdict?}
Rejections along the way: {rejections?}
Disease context: {disease_context?}
Shortage context: {shortage_context?}
Air context: {air_context?}

Output: (A) Ranked stops with numbers and scales. (B) "What we could not tell you".""",
    output_key="reviewed_plan",
)

communicator = LlmAgent(
    name="communicator",
    model=MODEL,
    description="Writes plain-language neighbourhood outreach.",
    instruction="""You write for community health workers to hand out on the street.
Rules: warm, confident, 6th-grade reading level. NO statistics, NO percentages, NO hedging
words like "estimated" or "modeled". Never name a disease about the reader or imply they
are sick. Say what is free, when, where, and that no insurance is needed.

Stops chosen: {ranked_tracts?}

Write a short flyer (under 90 words) and a 2-sentence text message, each in English and
Spanish. Use placeholders like [PARKING SPOT NEAR TRACT 13121001100] and [DATE] where you
do not know the real address or date.""",
    output_key="neighborhood_message",
)

plan_writer = LlmAgent(
    name="plan_writer",
    model=MODEL,
    description="Assembles the final Clinic Plan.",
    include_contents="none",
    instruction="""Assemble the Clinic Plan in Markdown with exactly these four sections:
1. Where the van goes and why
2. What we could not tell you
3. Who this plan lands on (equity audit, including any rejection and what changed)
4. The message for the neighborhood

Reviewed plan: {reviewed_plan?}
Audit findings: {audit_findings?}
Auditor verdict: {audit_verdict?}
Rejections: {rejections?}
Message: {neighborhood_message?}

Keep the reviewer's scale labels in sections 1-3 and the communicator's plain wording in
section 4. The two styles must stay different; do not blend them.""",
)


def _reset_round(callback_context: CallbackContext):
    """Fresh plan per request, so last turn's rejection doesn't leak into this one."""
    for k in ("ranked_tracts", "audit_findings", "audit_verdict", "revision_request",
              "rejections", "reviewed_plan", "neighborhood_message"):
        callback_context.state[k] = None
    callback_context.state["rejections"] = []
    return None


root_agent = SequentialAgent(
    name="clinic_broker",
    description="Plans mobile clinic stops for a county and explains them.",
    sub_agents=[context_scouts, plan_audit_loop, safety_reviewer, communicator, plan_writer],
    before_agent_callback=_reset_round,
)
