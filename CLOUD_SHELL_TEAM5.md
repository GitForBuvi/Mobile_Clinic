# Team 5: run and deploy from Cloud Shell

Cloud Shell is already signed in to the project, so no local login is needed.

## 1. Push from your Mac (VS Code terminal, repo root)
```bash
git checkout -b agent
git add agent/ scripts/check_setup.py scripts/run_demo.py deploy/ CLOUD_SHELL_TEAM5.md
git commit -m "Clinic Broker multi-agent ADK system"
git push -u origin agent
```

## 2. In Cloud Shell
```bash
git clone <your team repo URL> && cd <repo>
git checkout agent
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
sed -i "s/your-project-id/$(gcloud config get-value project)/" .env
# set COUNTY_NAME in .env if your team didn't load Fulton

python ../scripts/check_setup.py     # tables + ACS check
python ../scripts/run_demo.py         # full run in the terminal
adk web --host 0.0.0.0 --port 8080    # then Web Preview -> port 8080
```

## 3. Judges' "delete one agent" before/after
```bash
python ../scripts/run_demo.py --save
python ../scripts/run_demo.py --no-auditor --save
# compare outputs/clinic_plan_full.md vs outputs/clinic_plan_no_auditor.md
```

## 4. Deploy (from the repo root)
```bash
cd ..
bash deploy/cloud_run.sh
```
