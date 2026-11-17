"""One web service: the ADK agent API + our Clinic Broker UI.

  /           -> frontend/static/index.html (our UI)
  /dev-ui     -> ADK's built-in debug UI (still handy for judges' State tab)
  /run_sse    -> ADK streaming API the UI calls

Run in Cloud Shell (from the frontend folder, with the agent venv active):
    python server.py            # then Web Preview -> port 8080
"""
import os

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.cli.fast_api import get_fast_api_app

HERE = os.path.dirname(os.path.abspath(__file__))
# Locally the agent package lives in ../agent; in the container it is copied to /app/agents.
AGENTS_DIR = os.environ.get("AGENTS_DIR", os.path.join(HERE, "..", "agent"))

try:  # pick up GOOGLE_CLOUD_PROJECT / COUNTY_NAME from agent/.env when running locally
    from dotenv import load_dotenv

    load_dotenv(os.path.join(AGENTS_DIR, ".env"))
except ImportError:
    pass

app = get_fast_api_app(agents_dir=AGENTS_DIR, web=True)

# Replace ADK's "/" redirect with our page.
app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/"]
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/ui-config")
def ui_config():
    return {"county": os.environ.get("COUNTY_NAME", "Fulton"), "app": "clinic_agent"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
