"""Daily JobsDB AI job search -> Notion.

Runs the Apify JobsDB Scraper for new Hong Kong AI / RAG / LLM jobs, scores each job
by skill keywords, skips jobs already in Notion (same company + title + posted date),
adds the rest to the "AI Engineer Job Tracker" database and writes one row per run to
the "Job Search Run Log" database, including days with no new jobs.
"""
import datetime as dt
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

import requests

HKT = ZoneInfo("Asia/Hong_Kong")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
TRACKER_DB = os.environ.get("TRACKER_DB_ID", "394f380e4baf4e8ca6990c3ca0577f5d")
RUNLOG_DB = os.environ.get("RUNLOG_DB_ID", "0633fbdad51f47e687376ae221c2b2bb")
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
ACTOR_ID = "wP8VELMmEJgeP0ae1"  # blackfalcondata/jobsdb-scraper

SCRAPER_INPUT = {
    "query": '["AI engineer", "RAG", "LLM engineer", "machine learning engineer", "AI developer"]',
    "country": "HK",
    "dateRange": "7",
    "sortMode": "date",
    "maxResults": 40,
    "maxPages": 3,
    "includeDetails": True,
    "includeApplicantInsights": False,
    "descriptionFormat": "text",
    "descriptionMaxLength": 3000,
    # Incremental mode only returns jobs not seen in earlier runs. A dry run turns it
    # off so testing does not "use up" today's new jobs.
    "incrementalMode": not DRY_RUN,
    "stateKey": os.environ.get("STATE_KEY", "hk-ai-engineer-gh"),
    "skipReposts": True,
}

SKILLS = ["rag", "llm", "python", "fastapi", "langchain", "machine learning",
          "next.js", "react", "vector", "openai", "agent", "nlp"]
SENIOR = re.compile(r"senior|lead|manager|principal|head|director|architect", re.I)

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------- helpers
def hk_date(iso: str | None, plus_days: int = 0) -> str | None:
    if not iso:
        return None
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(HKT)
    return (d + dt.timedelta(days=plus_days)).date().isoformat()


def text(value: str, limit: int = 1900) -> list:
    value = (value or "")[:limit]
    return [{"type": "text", "text": {"content": value}}] if value else []


def notion(method: str, path: str, body: dict | None = None) -> dict:
    """Call the Notion API with simple retry on rate limits and server errors."""
    for attempt in range(5):
        r = requests.request(method, f"https://api.notion.com/v1/{path}",
                             headers=NOTION_HEADERS, json=body, timeout=60)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(float(r.headers.get("Retry-After", 2 * (attempt + 1))))
            continue
        if not r.ok:
            raise RuntimeError(f"Notion {method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()
    raise RuntimeError(f"Notion {method} {path} kept failing after retries")


# ---------------------------------------------------------------- steps
def fetch_jobs() -> list[dict]:
    url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
    r = requests.post(url, json=SCRAPER_INPUT, timeout=330,
                      headers={"Authorization": f"Bearer {APIFY_TOKEN}"})
    if not r.ok:
        raise RuntimeError(f"Apify -> {r.status_code}: {r.text[:500]}")
    data = r.json()
    return [j for j in data if isinstance(j, dict) and j.get("jobId")]


def shape(job: dict) -> dict:
    title = job.get("title") or "Untitled"
    body = f"{title} {job.get('description') or job.get('teaser') or ''}".lower()
    hits = [k for k in SKILLS if k in body]
    if SENIOR.search(title):
        fit = "低"
    elif len(hits) >= 4:
        fit = "高"
    elif len(hits) >= 2:
        fit = "中"
    else:
        fit = "低"

    arrangement = str(job.get("workArrangement") or "").lower()
    remote = "Remote" if "remote" in arrangement else "Hybrid" if "hybrid" in arrangement else "On-site"

    today = dt.datetime.now(HKT).date().isoformat()
    posted = hk_date(job.get("postedDate")) or today
    valid = hk_date(job.get("validThrough"))
    notes = [f"薪金: {job.get('salaryText') or '未列明'}"]
    if not valid:
        valid = hk_date(job.get("postedDate"), 30) or hk_date(dt.datetime.now(HKT).isoformat(), 30)
        notes.append("截止日期為估計（刊登後30日）")
    teaser = (job.get("teaser") or "")[:250]
    if teaser:
        notes.append(teaser)

    company = (job.get("company") or "").strip()
    return {
        "title": title,
        "company": company,
        "location": job.get("location") or "",
        "url": job.get("canonicalUrl") or job.get("sourceUrl") or "",
        "change": job.get("changeType") or "NEW",
        "remote": remote,
        "fit": fit,
        "skills": hits,
        "posted": posted,
        "valid": valid,
        "notes": " | ".join(notes),
        "key": "|".join([company.lower(), title.strip().lower(), posted]),
    }


def existing_keys() -> set[str]:
    keys, cursor = set(), None
    while True:
        body = {"page_size": 100,
                "filter": {"property": "Job Key", "rich_text": {"is_not_empty": True}}}
        if cursor:
            body["start_cursor"] = cursor
        res = notion("POST", f"databases/{TRACKER_DB}/query", body)
        for page in res.get("results", []):
            parts = page["properties"].get("Job Key", {}).get("rich_text", [])
            key = "".join(p.get("plain_text", "") for p in parts).strip().lower()
            if key:
                keys.add(key)
        if not res.get("has_more"):
            return keys
        cursor = res.get("next_cursor")


def add_job(job: dict) -> None:
    props = {
        "職位": {"title": text(job["title"])},
        "公司": {"rich_text": text(job["company"])},
        "地點": {"rich_text": text(job["location"])},
        "連結": {"url": job["url"] or None},
        "來源": {"select": {"name": "JobsDB"}},
        "狀態": {"select": {"name": "未申請"}},
        "配合度": {"select": {"name": job["fit"]}},
        "Remote": {"select": {"name": job["remote"]}},
        "搵到日期": {"date": {"start": dt.datetime.now(HKT).date().isoformat()}},
        "刊登日期": {"date": {"start": job["posted"]}},
        "截止日期": {"date": {"start": job["valid"]}},
        "Job Key": {"rich_text": text(job["key"])},
        "要求/備註": {"rich_text": text(job["notes"])},
    }
    notion("POST", "pages", {"parent": {"database_id": TRACKER_DB}, "properties": props})


def log_run(scraped: int, fits: int, added: int, note: str) -> None:
    today = dt.datetime.now(HKT).date().isoformat()
    title = f"{today} ✅ 新增 {added} 份" if added else f"{today} 📭 冇新職位"
    props = {
        "日期": {"title": text(title)},
        "Run Date": {"date": {"start": today}},
        "結果": {"select": {"name": "有新職位" if added else "冇新職位"}},
        "搵到職位": {"number": scraped},
        "符合條件": {"number": fits},
        "新增到 Tracker": {"number": added},
        "備註": {"rich_text": text(note)},
    }
    notion("POST", "pages", {"parent": {"database_id": RUNLOG_DB}, "properties": props})


# ---------------------------------------------------------------- main
def main() -> int:
    missing = [n for n, v in (("APIFY_TOKEN", APIFY_TOKEN), ("NOTION_TOKEN", NOTION_TOKEN)) if not v]
    if missing:
        print(f"Missing secrets: {', '.join(missing)}")
        return 1
    print(f"DRY_RUN={DRY_RUN}  stateKey={SCRAPER_INPUT['stateKey']}")

    try:
        raw = fetch_jobs()
    except Exception as exc:  # log the failure so the Run Log shows it, then fail the job
        print(f"Scraper failed: {exc}")
        if not DRY_RUN:
            log_run(0, 0, 0, f"❌ 出錯：Apify Scraper 失敗 - {str(exc)[:300]}")
        return 1

    jobs = [shape(j) for j in raw]
    fits = [j for j in jobs if j["fit"] != "低" and j["change"] != "UPDATED"]
    seen = existing_keys()
    new, batch = [], set()
    for j in fits:
        k = j["key"].lower()
        if k in seen or k in batch:
            continue
        batch.add(k)
        new.append(j)

    print(f"Scraped {len(jobs)} | good fits {len(fits)} | new to Notion {len(new)}")
    for j in jobs:
        mark = "ADD " if j in new else "skip"
        print(f"  [{mark}] {j['fit']} {j['posted']} {j['company']} - {j['title']}  {j['skills']}")

    if DRY_RUN:
        print("Dry run: nothing written to Notion.")
        return 0

    added = 0
    for j in new:
        add_job(j)
        added += 1

    if not jobs:
        note = "JobsDB 今日冇新刊登（或者全部之前已經見過）"
    elif not fits:
        note = f"搵到 {len(jobs)} 份新職位，但配合度都係「低」"
    elif not added:
        note = f"{len(fits)} 份符合條件，但 Notion 已經有記錄"
    else:
        note = f"新增 {added} 份到 AI Engineer Job Tracker"
    log_run(len(jobs), len(fits), added, note)
    print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
