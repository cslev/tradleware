# Tradleware
Autotrading Middleware mostly for Crypto/Stock Exchanges available and approved in Singapore.


## DEV: Run and debug

Navigate to project root
```
cd /path/to/tradleware
```

Activate virtual environment
```
source .venv/bin/activate
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