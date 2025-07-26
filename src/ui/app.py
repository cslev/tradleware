from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# You might need to adjust this import based on where your logger.py is relative to app.py
# If your logger is within src/misc, you might access it like this:
from src.misc.logger import CustomLogger

# Initialize FastAPI app
app = FastAPI(
  title="Tradleware Web UI",
  description="Web interface for the Tradleware trading bot middleware."
)

# Initialize a logger for the FastAPI app
# Ensure CustomLogger is correctly imported from src.misc.logger
logger = CustomLogger('Tradleware')

# Mount static files (for CSS, JS, images). Paths are now relative to /src/ui/
# BUT, FastAPI needs the path relative to the app's *startup directory*
# The 'directory="static"' refers to the '/static' folder.

app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")

# Configure Jinja2 templates. Similarly, path is relative to root.
templates = Jinja2Templates(directory="src/ui/templates")


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
  """
  Renders the main index.html page for the trading bot UI.
  """
  logger.info("Serving root page '/'")
  return templates.TemplateResponse(
      "index.html",
      {"request": request, "title": "Tradleware Dashboard", "message": "Welcome to Tradleware UI!"}
  )

@app.post("/webhook")
async def handle_webhook(data: dict):
  """
  Handles incoming webhooks (e.g., from tradingview or an exchange).
  """
  logger.info(f"Received webhook data: {data}")
  # Here, you would implement the logic to process the webhook,
  # e.g., trigger a trade, update internal state, log an event.
  return {"status": "success", "message": "Webhook received"}

# This __main__ block is mostly for quick local testing if you 'python src/ui/app.py'
# For robust running, you'll use 'uvicorn src.ui.app:app' from the BOLEHTRADE root.
if __name__ == "__main__":
  import uvicorn
  logger.info("Starting Uvicorn server for UI...")
  # Note: If running this directly, StaticFiles/Jinja2Templates paths
  # would need to be "static" and "templates" (relative to current file).
  # Running with 'uvicorn src.ui.app:app' from BOLEHTRADE root is preferred
  # as it simplifies path management.
  uvicorn.run(app, host="0.0.0.0", port=8080, reload=True, log_config=None, access_log=False)
