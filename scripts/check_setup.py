"""Run from the repo root:  python scripts/check_setup.py
Checks login, lists the a4i_health tables with row counts and columns, and checks
the public ACS table the equity auditor uses. Paste the output to your team/Claude."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
from clinic_agent import config  # loads agent/.env
from google.cloud import bigquery

if not config.PROJECT or config.PROJECT == "your-project-id":
    sys.exit("Set GOOGLE_CLOUD_PROJECT in agent/.env first.")
bq = bigquery.Client(project=config.PROJECT)
print(f"project: {config.PROJECT}   dataset: {config.DATASET}\n")
for t in bq.list_tables(f"{config.PROJECT}.{config.DATASET}"):
    tb = bq.get_table(t)
    print(f"{t.table_id:<22} {tb.num_rows:>7,} rows  | " + ", ".join(f.name for f in tb.schema))
print()
for acs in ("censustract_2020_5yr", "censustract_2019_5yr"):
    try:
        r = list(bq.query(
            "SELECT COUNT(*) n FROM `bigquery-public-data.census_bureau_acs." + acs + "` a "
            f"JOIN `{config.PROJECT}.{config.DATASET}.burden_tracts` b USING (geo_id) "
            "WHERE a.black_pop IS NOT NULL AND a.hispanic_pop IS NOT NULL").result())[0].n
        print(f"ACS {acs}: {r} of your tracts matched (used by the equity auditor)")
        break
    except Exception as e:
        print(f"ACS {acs}: not usable -> {str(e)[:150]}")
