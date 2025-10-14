# Tradleware
Autotrading Middleware mostly for Crypto/Stock Exchanges available and approved in Singapore.



## Features

- **Multi-Exchange Support**: Currently supports OKX, extensible for other exchanges
- **Web UI**: FastAPI-based web interface for monitoring and controlling trading bots
- **Real-time Logging**: Color-coded logs with real-time updates in the web interface
- **Webhook Integration**: Secure webhook endpoints for automated trading signals
- **Gotify Notifications**: Real-time push notifications for trading events
- **Convert Functionality**: Automatic fiat to stablecoin conversion

## Docker Deployment

The easiest way to run Tradleware is using Docker. The application includes a `Dockerfile` and `docker-compose.yml` for simple deployment.

### Build the Docker Image

Navigate to the project root and build the image:

```bash
cd /path/to/tradleware
docker-compose build
```

This will:
- Use Python 3.11 slim image as the base
- Install all dependencies from `src/requirement.txt`
- Set up the application with proper permissions
- Configure health checks for monitoring

### Configure Environment Variables

Before running the container, you need to set up your `.env` file with the necessary configuration:

1. **Copy the example environment file** (if available) or create a new `.env` file:
   ```bash
   cp .env.example .env  # If you have an example file
   # OR
   touch .env
   ```

2. **Configure your trading bot(s)**:
   ```env
   # Active bot configurations (comma-separated)
   ACTIVE_TRADING_CONFIGS="MYBTCBOT_OKX"
   
   # OKX Exchange Configuration
   MYBTCBOT_OKX_API_KEY="your-api-key"
   MYBTCBOT_OKX_SECRET_KEY="your-secret-key"
   MYBTCBOT_OKX_PASSPHRASE="your-passphrase"
   MYBTCBOT_OKX_HOSTNAME="www.okx.com"
   MYBTCBOT_OKX_SUBACCOUNT_NAME="YourSubaccountName"
   MYBTCBOT_OKX_FIAT_STABLECOIN_PAIR="USDT/USD"
   MYBTCBOT_OKX_CRYPTO_STABLECOIN_PAIR="BTC/USDT"
   
   # Generate webhook API key (use: openssl rand -hex 32)
   MYBTCBOT_OKX_TRADLEWARE_API_KEY="your-generated-api-key"
   ```

3. **Configure webhook security** (recommended):
   ```env
   # Generate random path (use: pwgen -n 14)
   WEBHOOK_PATH="ka8Moh4aiNgai4"
   ```

4. **Optional: Configure Gotify notifications**:
   ```env
   GOTIFY_SERVER_URL="https://your-gotify-server.com"
   GOTIFY_APP_TOKEN="your-gotify-token"
   GOTIFY_LOG_LEVEL=30
   ```
   *Gotify integration sends real-time push notifications to your mobile device or desktop for trading events (successful trades, errors, warnings). It's like receiving SMS alerts but through a self-hosted notification server.*

5. **Optional: Configure UI refresh rate**:
   ```env
   LOG_REFRESH_INTERVAL_MS=5000  # 5 seconds
   ```
   *This controls how often the web UI polls for new log messages from your trading bots. Lower values (e.g., 1000ms) provide near real-time updates but increase server load, while higher values (e.g., 10000ms) reduce load but delay log visibility.*

### Run the Container

After building, start the container:

```bash
docker-compose up -d
```

The application will be available at `http://localhost:8080`

### View Logs

Check the application logs:

```bash
docker-compose logs -f tradleware
```

### Stop the Container

```bash
docker-compose down
```

### Rebuild After Changes

If you make changes to the code or `.env` file:

```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

**Note:** The `docker-compose.yml` mounts the `./logs` directory to persist logs even when the container restarts.

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

## Webhook Security Configuration

Tradleware supports configurable webhook endpoints to protect against DDoS attacks and unauthorized access attempts. By default, webhooks are accessible at `/webhook`, but you can customize this to any random or meaningful path.

### Why Configure a Custom Webhook Path?

**Security Benefits:**
- **Obscurity**: Random webhook paths make it harder for attackers to discover your endpoint
- **DDoS Protection**: Without knowing the path, automated bots cannot target your webhook with spam requests
- **Brute Force Prevention**: Reduces the attack surface by making the endpoint URL unpredictable
- **No Code Changes**: You can change the path anytime by just updating the `.env` file

### How to Configure

1. **Generate a Random Webhook Path** (Recommended):
   ```bash
   # Generate a 14-character random string
   pwgen -n 14
   # Example output: ka8Moh4aiNgai4
   ```

2. **Add to `.env` file**:
   ```env
   # Default (less secure)
   WEBHOOK_PATH=webhook
   
   # Recommended: Use a random string
   WEBHOOK_PATH=ka8Moh4aiNgai4
   
   # Or use a custom meaningful path
   WEBHOOK_PATH=my-trading-signals-2024
   ```

3. **Update TradingView Webhook URL**:
   After changing the path, update your TradingView alerts to use the new webhook URL:
   ```
   https://your-tradleware-domain.com/ka8Moh4aiNgai4
   ```

### Webhook Endpoint Details

**Default Endpoint:**
- URL: `https://your-domain.com/webhook`
- Easy to guess, vulnerable to scanning

**Secured Endpoint (Example):**
- URL: `https://your-domain.com/ka8Moh4aiNgai4`
- Nearly impossible to guess, protected from random attacks

The webhook path is displayed in:
- The web UI on each trading bot card
- The footer of the dashboard
- The cURL test examples

**Note:** The webhook still requires API key authentication, so even if someone discovers the URL, they cannot execute trades without the correct `MYBTCBOT_OKX_TRADLEWARE_API_KEY`.



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