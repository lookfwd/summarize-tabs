import os
import pandas as pd
import requests
import hashlib
import json
from tqdm import tqdm
from pathlib import Path
from multiprocessing.pool import ThreadPool

INPUT_FILE = "toprocess.txt"
OUTPUT_FILE = "summaries.xlsx"
SOURCES_DIR = "sources"
SUMMARIES_DIR = "summaries"
CONCURRENCY = 10

MODEL = "openai/gpt-4.1-mini"  # or "openai/gpt-4.1"
MAX_CHARS = 8000  # safety limit per file


OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")  # set this in your shell
JINA_API_KEY = os.getenv("JINA_API_KEY")  # set this in your shell

# Validate API keys are set
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY environment variable is not set")
if not JINA_API_KEY:
    raise ValueError("JINA_API_KEY environment variable is not set")


def crawl(src_url):
    url = "https://r.jina.ai/" + src_url
    headers = {"Authorization": f"Bearer {JINA_API_KEY}", "X-Return-Format": "text"}

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return 200, response.text
    else:
        return response.status_code, ""


def scrape_api(args):
    idx, url = args

    # Compute MD5 hash of the URL
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
    file_name = f"{url_hash}.txt"
    output_file = os.path.join(SOURCES_DIR, file_name)

    if os.path.exists(output_file):
        # File already exists
        return idx, file_name, "exists"

    try:
        code, text = crawl(url)

        # +------+-------+-------------------------------+------------------------+
        # | Code | Times | Meaning                       | What usually causes it |
        # +------+-------+-------------------------------+------------------------+
        # | 403  | 1     | Forbidden                     | Rate-limiting, geo-blo |
        # | 422  | 4     | Unprocessable Entity          | Invalid/missing parame |
        # | 451  | 1     | Unavailable For Legal Reasons | Blocked legal/governme |
        # | 503  | 5     | Service Unavailable           | Server overloaded      |
        # | 524  | 1     | A Timeout Occurred            | Cloudflare server time |
        # +------+-------+-------------------------------+------------------------+
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)

        return idx, file_name, code

    except Exception as e:
        # On error, still log it
        return idx, file_name, f"error: {str(e)}".replace('"', '""')


def call_openrouter_for_file(text: str) -> dict:
    """
    Sends the file content to OpenRouter and returns a dict:
      {
        "status": "content missing" or "summary",
        "summary": "<three sentence summary or empty string>"
      }
    """
    # Truncate very large files just to be safe
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    system_prompt = (
        "You will receive text scraped from a web page. "
        "Sometimes it is mostly boilerplate (navigation menus, login prompts, "
        "error messages, CAPTCHAs, or 'unusual traffic' messages). "
        "Other times it includes real content (articles, tables, transcripts, etc.).\n\n"
        "Your task:\n"
        "1. Decide if the text contains meaningful page content.\n"
        "   - If it is mostly boilerplate, navigation, or an error/anti-bot page, "
        "     treat it as content missing.\n"
        "   - If it contains substantial real content (even partial), treat it as content present.\n"
        "2. If content is missing, respond with:\n"
        '   {"status": "content missing", "summary": ""}\n'
        "3. If content is present, respond with:\n"
        '   {"status": "summary", "summary": "<exactly three sentences summarizing the content>"}\n\n'
        "Important:\n"
        "- The JSON must be valid and parseable.\n"
        "- 'status' must be exactly either 'content missing' or 'summary'.\n"
        "- If 'status' is 'summary', 'summary' must be exactly three sentences, no bullet points.\n"
        "- Don't introduce the 'summary' with 'the text' or 'the scraped text' or equivalent. e.g. \n"
        "  instead of 'The text is a detailed product listing' just say 'a detailed product listing'."
    )

    user_prompt = (
        "Here is the scraped text from a file. Analyze it according to the instructions.\n\n"
        "SCRAPED TEXT START\n"
        f"{text}\n"
        "SCRAPED TEXT END"
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "summarize-tabs.py",
        "X-Title": "File Summarizer Script",
    }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 300,
        "temperature": 0.0,
    }

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        data=json.dumps(payload),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    content = data["choices"][0]["message"]["content"].strip()

    # Sometimes models wrap JSON in markdown; strip that if needed
    if content.startswith("```"):
        # remove ```json or ``` and trailing ```
        content = content.strip("`")
        # occasionally something like "json\n{...}"
        if "\n" in content:
            content = content.split("\n", 1)[1]

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Fallback: if parsing fails, treat as content missing
        return {"status": "content missing", "summary": ""}

    # Normalize keys
    status = result.get("status", "").strip().lower()
    summary = result.get("summary", "").strip()

    if status not in ("content missing", "summary"):
        # Fallback if model didn't follow instructions
        return {"status": "content missing", "summary": ""}

    if status == "content missing":
        return {"status": "content missing", "summary": ""}

    # status == "summary"
    return {"status": "summary", "summary": summary}


def summarize_api(args):
    idx, file_name = args
    input_file = Path(os.path.join(SOURCES_DIR, file_name))

    try:
        text = input_file.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return idx, f"file '{input_file.name}' missing"

    try:
        result = call_openrouter_for_file(text)
    except Exception as e:
        return idx, f"Error calling OpenRouter for {input_file.name}: {e}"

    output_file = os.path.join(SUMMARIES_DIR, file_name)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(result["summary"])

    return idx, result["status"]


def process_all(f, f_name, todo):
    with ThreadPool(CONCURRENCY) as pool:
        for i in tqdm(
            pool.imap_unordered(f, todo), total=len(todo), desc=f_name, unit="url"
        ):
            yield i


def input_urls(fname):
    urls = []
    with open(fname, "r", encoding="utf-8") as infile:
        for line in infile:
            line = line.strip()
            if not line or "|" not in line:
                continue

            url = line.split("|", 1)[0].strip()
            if not url:
                continue

            urls.append(url)
    return urls


def normalize_empty(series: pd.Series) -> pd.Series:
    """Return a boolean mask where values are considered empty (NaN or empty/whitespace string)."""
    return series.isna() | (series.astype(str).str.strip() == "")


def update_index(fname, urls):
    if not os.path.exists(fname):
        columns = ["url", "file", "status", "summary"]
        df = pd.DataFrame(columns=columns)
        df.to_excel(fname, sheet_name="links", index=False)

    df = pd.read_excel(fname)
    df["url"] = df["url"].astype(str)

    # ---------------- PASS 1 ----------------
    # If the URL is not there, add it with all other columns empty
    existing_urls = set(df["url"])
    new_rows = []
    for url in urls:
        if url not in existing_urls:
            new_rows.append(
                {
                    "url": url,
                    "file": "",
                    "status": "",
                    "summary": "",
                }
            )
    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)

    # ---------------- PASS 2 ----------------
    # For every row that doesn't have status set:
    #   set "status" and "file"
    status_empty_mask = normalize_empty(df["status"])

    todo = [(idx, df.at[idx, "url"]) for idx in df[status_empty_mask].index]
    for idx, fname, status in process_all(scrape_api, "Processing URLs", todo):
        df.at[idx, "status"] = status
        df.at[idx, "file"] = fname

    # ---------------- PASS 3 ----------------
    # For every row that doesn't have summary:
    #   set "summary"])
    content_empty_mask = normalize_empty(df["summary"])

    todo = [(idx, df.at[idx, "file"]) for idx in df[content_empty_mask].index]
    for idx, summary in process_all(summarize_api, "Summarizing URLs", todo):
        df.at[idx, "summary"] = summary

    df = df.sort_values(by=["status", "url"], ascending=[False, True])

    df.to_excel(OUTPUT_FILE, sheet_name="links", index=False)


# Ensure aux directories exists
os.makedirs(SOURCES_DIR, exist_ok=True)
os.makedirs(SUMMARIES_DIR, exist_ok=True)

urls = input_urls(INPUT_FILE)

update_index(OUTPUT_FILE, urls)
