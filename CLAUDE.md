# EdTech News Monitor

## What this does
Monitors TES, Schools Week, and BBC News (Education & Technology sections) for articles about EdTech companies that offer teaching and learning tools. Sends email alerts via Postmark when new matching articles are found.

## How to run
```bash
POSTMARK_API_TOKEN=<token> python3 monitor.py
```

## Required environment variables
- `POSTMARK_API_TOKEN` — Postmark Server API token
- `EMAIL_TO` — recipient email (default: gregadam@gmail.com)
- `EMAIL_FROM` — sender email, must be verified in Postmark (default: noreply@lbq.org)

## How it works
1. Fetches RSS feeds from TES, Schools Week, BBC Education, BBC Technology
2. Falls back to HTML scraping if RSS fails
3. Filters articles using two-tier keyword matching (must match EdTech signals AND teaching/learning signals)
4. Tracks seen articles in `seen_articles.json` to avoid duplicate emails
5. Sends digest email via Postmark API
