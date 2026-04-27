#!/usr/bin/env python3
"""
Fetch GitHub stats for a username and replace the stats block in README.md.

Notes:
- Set TARGET_USER via environment (the workflow sets it).
- Uses GITHUB_TOKEN from the environment (provided automatically in Actions).
"""

import os
import re
import requests
from urllib.parse import urlparse, parse_qs

GITHUB_API = "https://api.github.com"
TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}
TARGET_USER = os.getenv("TARGET_USER") or os.getenv("GITHUB_ACTOR")

README_PATH = "README.md"
STATS_START = "<!-- STATS_START -->"
STATS_END = "<!-- STATS_END -->"

def paged_get(url, params=None, max_pages=10):
    """Yield items from a paginated endpoint (assumes items array)."""
    params = params or {}
    page = 1
    while page <= max_pages:
        params.update({"per_page": 100, "page": page})
        r = requests.get(url, headers=HEADERS, params=params)
        r.raise_for_status()
        items = r.json()
        if not items:
            break
        for it in items:
            yield it
        if len(items) < 100:
            break
        page += 1

def get_user_repos(user):
    url = f"{GITHUB_API}/users/{user}/repos"
    repos = []
    page = 1
    while True:
        r = requests.get(url, headers=HEADERS, params={"per_page": 100, "page": page, "type": "owner"})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos

def get_top_languages(repos):
    lang_bytes = {}
    for repo in repos:
        name = repo["name"]
        owner = repo["owner"]["login"]
        r = requests.get(f"{GITHUB_API}/repos/{owner}/{name}/languages", headers=HEADERS)
        if r.status_code != 200:
            continue
        data = r.json()
        for lang, b in data.items():
            lang_bytes[lang] = lang_bytes.get(lang, 0) + b
    sorted_langs = sorted(lang_bytes.items(), key=lambda x: x[1], reverse=True)
    return [f"{lang} ({bytes_} bytes)" for lang, bytes_ in sorted_langs[:6]]

def count_commits_by_author(user, repos):
    total = 0
    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        r = requests.get(f"{GITHUB_API}/repos/{owner}/{name}/commits",
                         headers=HEADERS,
                         params={"author": user, "per_page": 1})
        if r.status_code == 200:
            commits = r.json()
            if not commits:
                continue
            link = r.headers.get("Link", "")
            if 'rel=\"last\"' in link:
                parts = link.split(",")
                last_url = None
                for p in parts:
                    if 'rel=\"last\"' in p:
                        m = re.search(r'<([^>]+)>', p)
                        if m:
                            last_url = m.group(1)
                if last_url:
                    parsed = urlparse(last_url)
                    q = parse_qs(parsed.query)
                    last_page = int(q.get("page", ["1"])[0])
                    total += last_page
                else:
                    total += len(commits)
            else:
                total += len(commits)
    return total

def search_total(q):
    r = requests.get(f"{GITHUB_API}/search/issues", headers=HEADERS, params={"q": q})
    r.raise_for_status()
    return r.json().get("total_count", 0)

def count_push_events_recent(user, max_pages=3):
    url = f"{GITHUB_API}/users/{user}/events/public"
    push_count = 0
    for page in range(1, max_pages+1):
        r = requests.get(url, headers=HEADERS, params={"per_page": 100, "page": page})
        if r.status_code != 200:
            break
        events = r.json()
        if not events:
            break
        for ev in events:
            if ev.get("type") == "PushEvent":
                push_count += 1
        if len(events) < 100:
            break
    return push_count

def get_followers(user):
    r = requests.get(f"{GITHUB_API}/users/{user}", headers=HEADERS)
    if r.status_code != 200:
        return 0
    return r.json().get("followers", 0)

def replace_stats_block(readme_text, md_table):
    pattern = re.compile(re.escape(STATS_START) + ".*?" + re.escape(STATS_END), re.S)
    new_block = f"{STATS_START}\n{md_table}\n{STATS_END}"
    new_text = pattern.sub(new_block, readme_text)
    return new_text

def make_markdown_table(commits, prs_open, prs_merged, issues_open, pushes, followers, top_langs):
    top_langs_str = ", ".join(top_langs) if top_langs else "None"
    table = (
        "| Metric | Value |\n"
        "|---|---|\n"
        f"| Commits (authored) | {commits} |\n"
        f"| PRs opened | {prs_open} |\n"
        f"| PRs merged | {prs_merged} |\n"
        f"| Issues opened | {issues_open} |\n"
        f"| Push events (recent) | {pushes} |\n"
        f"| Followers | {followers} |\n"
        f"| Top languages | {top_langs_str} |\n"
    )
    return table

def main():
    if not TARGET_USER:
        print("TARGET_USER is not set. Exiting.")
        return

    print(f"Collecting stats for {TARGET_USER}...")

    repos = get_user_repos(TARGET_USER)
    print(f"Found {len(repos)} repos owned by user (scanning languages and commits).")

    top_langs = get_top_languages(repos)

    commits = count_commits_by_author(TARGET_USER, repos)
    print(f"Total commits authored (approx): {commits}")

    prs_open = search_total(f"author:{TARGET_USER} type:pr")
    prs_merged = search_total(f"author:{TARGET_USER} type:pr is:merged")
    issues_open = search_total(f"author:{TARGET_USER} type:issue")
    pushes = count_push_events_recent(TARGET_USER)
    followers = get_followers(TARGET_USER)

    md_table = make_markdown_table(commits, prs_open, prs_merged, issues_open, pushes, followers, top_langs)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme_text = f.read()

    if STATS_START not in readme_text or STATS_END not in readme_text:
        print("README does not contain expected STATS_START/STATS_END markers. Exiting.")
        return

    new_readme = replace_stats_block(readme_text, md_table)

    if new_readme != readme_text:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(new_readme)
        print("README updated with new stats.")
    else:
        print("No changes to README (stats unchanged).")

if __name__ == "__main__":
    main()
