# Team 5 agent: run it locally

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then put your project id in .env
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
python ../scripts/check_setup.py   # tables + ACS check
adk web                            # open http://localhost:8000, pick clinic_agent
```

Try: `Plan four Saturday stops for the mobile clinic van.`

Demo points for judges (open the State tab in adk web):
- `ranked_tracts` written by **planner**, read by **equity_auditor**
- `revision_request` written by **equity_auditor**, read by **planner** (rejected first answer)
- `safety_reviewer` vs `communicator`: deliberately conflicting instructions
- Scouts use the Google-managed **MCP Toolbox for Databases** (BigQuery prebuilt)
- `rank_tracts_for_van` / `audit_plan_equity` are our own Python tools
