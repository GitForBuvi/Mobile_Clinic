"""Run the Clinic Broker from the terminal and print the evidence judges ask for.

From the repo root (Cloud Shell or anywhere with gcloud credentials):
    python scripts/run_demo.py                  # full system
    python scripts/run_demo.py --no-auditor     # judges' Q1: delete one agent
    python scripts/run_demo.py --save           # also write outputs/clinic_plan_*.md

Prints: the stops, every rejection the auditor made, the audit verdict, and the final
Clinic Plan. Compare the two runs side by side for the before/after slide.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

from google.adk.agents import SequentialAgent  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from clinic_agent import agent as A  # noqa: E402

PROMPT = "Plan four Saturday stops for the mobile clinic van, and explain them."


def build(no_auditor: bool):
    if not no_auditor:
        return A.root_agent
    # Same agents minus the equity auditor: the planner's first answer goes straight out.
    return SequentialAgent(
        name="clinic_broker_no_auditor",
        sub_agents=[A.context_scouts.clone(), A.planner.clone(),
                    A.safety_reviewer.clone(), A.communicator.clone(),
                    A.plan_writer.clone()],
        before_agent_callback=A._reset_round,
    )


async def main(no_auditor: bool, save: bool, prompt: str):
    root = build(no_auditor)
    runner = InMemoryRunner(agent=root, app_name="clinic")
    session = await runner.session_service.create_session(app_name="clinic", user_id="demo")
    t0, final = time.time(), ""
    async for ev in runner.run_async(
            user_id="demo", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)])):
        if ev.content and ev.content.parts:
            for p in ev.content.parts:
                if p.function_call:
                    print(f"  [{ev.author}] -> tool {p.function_call.name}"
                          f"({json.dumps(dict(p.function_call.args or {}))[:120]})")
                elif p.text and ev.author == root.sub_agents[-1].name:
                    final += p.text
    s = (await runner.session_service.get_session(
        app_name="clinic", user_id="demo", session_id=session.id)).state

    print("\n=== STOPS (state['ranked_tracts']) ===")
    for st in (s.get("ranked_tracts") or {}).get("stops", []):
        ev = st["evidence"]
        print(f"  #{st['rank']} {st['geo_id']}  score {st['need_score']}  pop {st['total_pop']}"
              f"  uninsured {ev['pct_uninsured_18_64']['value']}% [tract]"
              f"  {ev['km_to_safety_net']['value']} km to safety-net [facility]")
    print("\n=== REJECTIONS (state['rejections']) ===")
    print(json.dumps(s.get("rejections") or [], indent=2) if s.get("rejections") else "  none")
    print("\n=== AUDIT VERDICT ===\n ", s.get("audit_verdict") or "  (no auditor in this run)")
    print(f"\n=== CLINIC PLAN ({time.time() - t0:.0f}s) ===\n{final}")

    if save:
        os.makedirs("outputs", exist_ok=True)
        tag = "no_auditor" if no_auditor else "full"
        path = f"outputs/clinic_plan_{tag}.md"
        with open(path, "w") as f:
            f.write(final)
        with open(f"outputs/state_{tag}.json", "w") as f:
            json.dump({k: v for k, v in s.items() if not k.startswith("_")}, f,
                      indent=2, default=str)
        print(f"\nsaved {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-auditor", action="store_true")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--prompt", default=PROMPT)
    a = ap.parse_args()
    asyncio.run(main(a.no_auditor, a.save, a.prompt))
