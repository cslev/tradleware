# Tradleware
Autotrading Middleware mostly for Crypto/Stock Exchanges available and approved in Singapore.

## Features

- **Multi-Exchange Support**: Currently supports OKX, extensible for other exchanges
- **Web UI**: FastAPI-based web interface for monitoring and controlling trading bots
- **Real-time Logging**: Color-coded logs with real-time updates in the web interface
- **Webhook Integration**: Secure webhook endpoints for automated trading signals
- **Gotify Notifications**: Real-time push notifications for trading events
- **Convert Functionality**: Automatic fiat to stablecoin conversion

## Gotify Integration

Tradleware supports real-time push notifications via Gotify for important trading events.

### Setup Gotify Notifications

1. **Install Gotify Server** (if you don't have one):
   ```bash
   # Using Docker
   docker run -p 80:80 gotify/server
   ```

2. **Configure Environment Variables**:
   Add these to your `.env` file:
   ```env
   GOTIFY_SERVER_URL=https://your-gotify-server.com
   GOTIFY_APP_TOKEN=your_app_token_here
   GOTIFY_LOG_LEVEL=30
   ```

## DEV: Run and debug

Navigate to project root
```
cd /path/to/tradleware
```

Activate virtual environment
```
source .venv/bin/activate
```

Install dependencies
```
pip install -r src/requirement.txt
```

Start Tailwind CSS watcher (in separate terminal)
```
cd src/ui
npx tailwindcss -i ./static/css/input.css -o ./static/css/output.css --watch
```

Run FastAPI app (in main terminal)
```
uvicorn src.ui.app:app --host 0.0.0.0 --port 8080 --reload
```