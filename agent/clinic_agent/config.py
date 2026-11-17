"""Settings, read once from agent/.env (see .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("A4I_DATASET", "a4i_health")
COUNTY_NAME = os.environ.get("COUNTY_NAME", "Fulton")
MODEL = os.environ.get("MODEL", "gemini-3.6-flash")

# Command that starts the Google-managed MCP Toolbox server for BigQuery.
# Default uses npx (Node is already on your Mac). If you download the toolbox
# binary instead, set TOOLBOX_CMD=/path/to/toolbox in .env (faster startup).
TOOLBOX_CMD = os.environ.get("TOOLBOX_CMD", "npx")


def table(name: str) -> str:
    """Fully qualified table name, back-ticked for SQL."""
    return f"`{PROJECT}.{DATASET}.{name}`"
