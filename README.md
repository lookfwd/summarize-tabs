# Bulk Web Page Summarizer

Works well with OneTab export format. This repository contains a Python script that:

* Reads a list of URLs from a text file
* Uses Jina AI to scrape the page content
* Uses OpenRouter/ChatGPT to decide whether the page has meaningful content and if it does, generates an exactly three-sentence summary
* Saves everything to an Excel index

It is designed to be incremental and resumable: you can re-run it with the same input file and it will only process new/unfinished URLs.

## API keys

* `OPENROUTER_API_KEY` – for the OpenRouter LLM API
* `JINA_API_KEY` – for the Jina AI reader API

```
export OPENROUTER_API_KEY="your-openrouter-key"
export JINA_API_KEY="your-jina-key"
```

## Dependencies

You can install dependencies with `pip install -r requirements.txt`

## Input file

Input format based on OneTab export format (toprocess.txt):

Each line should contain: `<URL> | label or note`

## Usage

```
python summarize-tabs.py
```

## Optional Configuration

At the top of the script you'll find configurable constants:

```
INPUT_FILE = "toprocess.txt"
OUTPUT_FILE = "summaries.xlsx"
SOURCES_DIR = "sources"
SUMMARIES_DIR = "summaries"
CONCURRENCY = 10

MODEL = "openai/gpt-4.1-mini"  # or "openai/gpt-4.1"
MAX_CHARS = 8000               # safety limit per file
```
