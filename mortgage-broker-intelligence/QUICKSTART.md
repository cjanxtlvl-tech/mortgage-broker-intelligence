# Quick Start

This guide gets the Mortgage Broker Intelligence dashboard running in minutes.

## 1) Install Dependencies

From the project folder:

```bash
pip install -r requirements.txt
```

## 2) Configure Environment

```bash
cp .env.example .env
```

Default settings in `.env.example` are ready for a first run.

## 3) Configure Password

Create `.streamlit/secrets.toml`:

```toml
APP_PASSWORD = "change-me-now-2026"
```

Change this password before sharing internally.

## 4) Run The Web App

```bash
streamlit run app.py
```

Open the local URL shown in terminal (usually `http://localhost:8501`) and enter your password.

## 5) Run Analysis In The App

1. Choose source mode:
   - `API` for live HMDA download
   - `Upload CSV` for local file mode
2. Set year, target states, excluded licensed states, and minimum loans.
3. Click `Run analysis`.
4. Download full and per-state CSV exports from the app.

## CLI Quick Commands

API mode:

```bash
python scripts/run_seed_build.py --source api --year 2024 --states FL,TX
```

Local mode:

```bash
python scripts/run_seed_build.py --source local --input data/raw/hmda.csv
```

## Deploy To Streamlit Community Cloud

1. Push your repository to GitHub.
2. Visit `https://share.streamlit.io`.
3. Connect your repo.
4. Select:
   - branch: `main`
   - app file: `app.py`
5. Add secret in Streamlit Cloud app settings:

```toml
APP_PASSWORD = "your-production-password"
```

Streamlit Cloud handles HTTPS, SSL/TLS, dependency installation, and redeployments automatically.

## Quick Troubleshooting

- `Invalid value: File does not exist: app.py`:
  Run Streamlit from the project root, or use an absolute path to `app.py`.
- `APP_PASSWORD is not configured`:
  Ensure `.streamlit/secrets.toml` exists locally or Streamlit Cloud secrets are set.
- API errors:
  Verify year/states are provided and internet access to FFIEC/CFPB is available.
