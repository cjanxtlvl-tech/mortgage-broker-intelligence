# Mortgage Broker Intelligence

A lightweight Streamlit internal intelligence dashboard for identifying high-volume mortgage companies by state using public HMDA/CFPB data.

Start here for the fastest setup: [QUICKSTART.md](QUICKSTART.md)

## Purpose

This tool helps non-technical users quickly:

- pull HMDA data by year and states
- rank lenders by originated production volume
- exclude states where your business is already licensed
- review top lenders per state in a web dashboard
- enrich company names and LEI metadata from GLEIF when HMDA rows include an LEI
- export clean CSV files for downstream workflows

## Why HMDA Is Used

HMDA data is public, regulator-backed, and structured for consistent analysis. It offers repeatable lender-level loan production signals (volume, count, purchase mix, FHA mix) without relying on brittle web scraping.

## LEI Enrichment

When HMDA rows include an `lei`, the app can query the GLEIF LEI registry and enrich the dashboard with company metadata.

GLEIF is a public, open LEI reference source. This helps improve HMDA rows where the lender name is missing, blank, or shown as `unknown`.

The enrichment adds:

- `gleif_legal_name`
- `gleif_entity_status`
- `gleif_registration_status`
- `gleif_jurisdiction`
- `gleif_legal_address`
- `gleif_headquarters_address`
- `gleif_last_update`
- `gleif_next_renewal_date`
- `gleif_managing_lou`

If `company_name` is blank, null, or `unknown`, the app replaces it with `gleif_legal_name` and preserves the original value in `original_company_name`.

The enrichment is rate-limit friendly:

- deduplicates LEIs before querying
- checks a local cache first
- saves successful lookups to `data/processed/lei_cache.json`
- uses a 15 second timeout
- pauses briefly between uncached calls
- limits lookups with the sidebar control

## Why This Avoids Paid Ranking Subscriptions

This app uses open FFIEC/CFPB HMDA data and transparent pandas-based scoring logic, so your team can:

- avoid paid leaderboard subscriptions for baseline ranking
- inspect and customize ranking logic directly
- maintain full control over export and enrichment workflows

## Tech Stack

- Python
- Streamlit
- pandas
- requests
- python-dotenv

## Project Structure

```text
mortgage-broker-intelligence/
  app.py
  README.md
  TODO.md
  requirements.txt
  .env.example
  .gitignore
  data/
    raw/
    processed/
    processed/by_state/
  src/
    config.py
    hmda_client.py
    lei_client.py
    transform.py
    scoring.py
    exporters.py
    utils.py
  scripts/
    run_seed_build.py
```

## Environment Configuration

Copy the template and edit values for your market focus:

```bash
cp .env.example .env
```

Template:

```env
HMDA_YEAR=2024
TARGET_STATES=FL,TX,GA,NC,SC
EXCLUDE_LICENSED_STATES=NJ,PA,NY
MIN_ORIGINATED_LOANS=25
OUTPUT_PATH=data/processed/mortgage_company_seed.csv
```

## Run Locally

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure local secrets for password protection:

```toml
# .streamlit/secrets.toml
APP_PASSWORD = "your-internal-password"
```

4. Launch the Streamlit app:

```bash
streamlit run app.py
```

## Streamlit UI Behavior

- Sidebar controls:
  - HMDA year
  - target states
  - exclude licensed states
  - minimum originated loans
  - optional LEI enrichment
  - max LEI lookups
  - source mode (API or Upload CSV)
- Main area:
  - run analysis button
  - top lenders by state
  - sortable full dataset
  - summary cards
  - charts for volume, FHA-heavy, and purchase-heavy lenders
  - LEI and company lookup with clickable LEI links to GLEIF
  - downloadable CSV outputs

## HMDA API Usage

Base URL:

- https://ffiec.cfpb.gov/v2/data-browser-api

Implemented in src/hmda_client.py:

- get_filers(year, states)
- download_csv(year, states, actions_taken="1")
- get_aggregations(year, states, **filters)

Implementation includes:

- request retries
- request timeouts
- streaming CSV downloads
- request URL logging
- defensive API error handling

CSV download mode uses actions_taken=1 for originated loans and saves raw files to data/raw.

## GLEIF LEI API Usage

Base URL:

- https://api.gleif.org/api/v1/lei-records/{lei}

Implemented in src/lei_client.py:

- get_lei_record(lei)
- enrich_dataframe_with_lei(df)

The client uses a local JSON cache at `data/processed/lei_cache.json` to avoid repeated calls during a run and across future runs.

## CLI Support

API mode:

```bash
python scripts/run_seed_build.py --source api --year 2024 --states FL,TX
```

Local CSV mode:

```bash
python scripts/run_seed_build.py --source local --input data/raw/hmda.csv
```

## Deploy To Streamlit Community Cloud

1. Push to GitHub.
2. Go to https://share.streamlit.io
3. Connect your repository.
4. Select:
   - branch: main
   - main file path: app.py
5. Deploy.

Streamlit Community Cloud automatically handles:

- HTTPS
- SSL/TLS
- dependency installation
- redeployments from GitHub updates

## Secrets Configuration In Streamlit Cloud

In your app settings, add:

```toml
APP_PASSWORD = "your-internal-password"
```

The app blocks access until the entered password matches APP_PASSWORD.

## Updating The App Through GitHub

- Commit changes to main (or merge to main via PR).
- Streamlit Cloud detects updates and redeploys automatically.

## GitHub Setup

```bash
git init
git add .
git commit -m "Initial mortgage intelligence dashboard"
gh repo create mortgage-broker-intelligence --private --source=. --remote=origin --push
```
