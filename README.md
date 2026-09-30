# Daily JobsDB AI Job Search

Every day at 18:00 Hong Kong time, GitHub Actions:

1. Runs the Apify **JobsDB Scraper** for new Hong Kong jobs (AI engineer, RAG, LLM engineer, machine learning engineer, AI developer) posted in the last 7 days.
2. Scores each job by skill keywords (RAG, LLM, Python, FastAPI, LangChain, React, vector DB, agents, NLP…). Senior / lead / manager roles are scored low.
3. Skips jobs already in Notion (same company + title + posted date).
4. Adds new medium/high-fit jobs to the **AI Engineer Job Tracker** database in Notion.
5. Writes one row per run to the **Job Search Run Log** database, including days with no new jobs.

## Setup

1. Repository → **Settings → Secrets and variables → Actions → New repository secret**:
   - `APIFY_TOKEN`: Apify API token
   - `NOTION_TOKEN`: Notion internal integration secret (or personal access token)
2. If you use a Notion integration, share both databases with it (••• → Connections).
3. **Actions** tab → *Daily JobsDB AI Job Search* → **Run workflow** (dry run first).

## Run locally

```bash
pip install -r requirements.txt
APIFY_TOKEN=... NOTION_TOKEN=... DRY_RUN=true python job_search.py
```

Tech: Python, REST APIs (Apify, Notion), GitHub Actions scheduling.
