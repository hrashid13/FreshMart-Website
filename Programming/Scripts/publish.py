"""
Run this after a ChatDev grocery store workflow completes.
It finds the latest session, extracts the HTML and CSS, and pushes to GitHub.

Usage:
    cd "C:/Users/hfras/Desktop/ChatDev Repo/ChatDev"
    uv run python publish.py
"""

import glob
import json
import os
import re
import subprocess
from datetime import datetime

import requests
import yaml


REPO_PATH = r"C:\Users\hfras\Desktop\FreshMart-Website"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = "hrashid13/FreshMart-Website"
BASE_BRANCH = "main"
WAREHOUSE = r"C:\Users\hfras\Desktop\ChatDev Repo\ChatDev\WareHouse"


def run(cmd, cwd=REPO_PATH):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(str(cmd) + " failed:\n" + result.stderr)
    return result.stdout.strip()


def latest_session():
    folders = glob.glob(os.path.join(WAREHOUSE, "session_*"))
    if not folders:
        raise FileNotFoundError("No session folders found in " + WAREHOUSE)
    return max(folders, key=os.path.getmtime)


def get_agent_text(node_outputs, node_id):
    node = node_outputs.get("node_" + node_id, {})
    collected = []
    for result in node.get("results", []):
        payload = result.get("payload", {})
        content = payload.get("content", [])
        if isinstance(content, str) and len(content) > 10:
            collected.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        collected.append(text)
                elif isinstance(block, str) and len(block) > 10:
                    collected.append(block)
    return "\n".join(collected)


def clean(text):
    return text.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')


def extract_html(text):
    text = clean(text)
    match = re.search(r'(<!DOCTYPE html>.*?</html>)', text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r'(<html.*?</html>)', text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    if '<header>' in text or '<section' in text:
        return text.strip()
    raise ValueError("No valid HTML found in Coder Agent output.")


def extract_css(text):
    if not text:
        return None
    text = clean(text)
    text = re.sub(r'</?style[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<!DOCTYPE[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.strip()
    if re.search(r'[a-zA-Z#.\-]+\s*\{', text):
        return text
    return None


def extract_theme(text):
    match = re.search(r'THEME[:\s]+([^\n]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "Weekly Update"


DEFAULT_CSS = """body { font-family: Arial, sans-serif; margin: 0; padding: 0; background: #fff; }
header { background-color: #2e7d32; color: white; text-align: center; padding: 1.5em; }
header h1 { margin: 0; font-size: 2em; }
header p { margin: 0.25em 0 0; font-size: 1.1em; }
#deals { display: flex; flex-wrap: wrap; justify-content: center; padding: 1.5em; gap: 1em; }
.card { background: white; border: 2px solid #2e7d32; border-radius: 8px; padding: 1em; min-width: 180px; max-width: 220px; box-shadow: 0 2px 6px rgba(0,0,0,0.1); }
.card h2 { color: #2e7d32; font-size: 1em; margin: 0 0 0.5em; }
.card p { margin: 0; font-size: 0.9em; }
#recipe { background: #e8f5e9; padding: 1.5em; margin: 1em auto; max-width: 700px; border-radius: 8px; }
#announcement { background: #e8f5e9; padding: 1.5em; margin: 1em auto; max-width: 700px; border-radius: 8px; }
footer { text-align: center; padding: 1em; background: #2e7d32; color: white; margin-top: 2em; }"""


def main():
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN environment variable is not set.")

    session = latest_session()
    print("Using session: " + os.path.basename(session))

    outputs_path = os.path.join(session, "node_outputs.yaml")
    with open(outputs_path, "r", encoding="utf-8") as f:
        node_outputs = yaml.safe_load(f)

    idea_text = get_agent_text(node_outputs, "Idea Agent")
    coder_text = get_agent_text(node_outputs, "Coder Agent")
    style_text = get_agent_text(node_outputs, "Style Agent")

    print("Idea text length: " + str(len(idea_text)))
    print("Coder text length: " + str(len(coder_text)))
    print("Style text length: " + str(len(style_text)))

    theme = extract_theme(idea_text)
    print("Theme: " + theme)

    html = extract_html(coder_text)
    print("HTML extracted: " + str(len(html)) + " chars")

    css = extract_css(style_text)
    if not css:
        print("Style Agent output unusable - using default CSS.")
        css = DEFAULT_CSS
    else:
        print("CSS extracted: " + str(len(css)) + " chars")

    with open(os.path.join(REPO_PATH, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(REPO_PATH, "style.css"), "w", encoding="utf-8") as f:
        f.write(css)
    print("Files written to repo.")

    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_theme = theme.lower().replace(" ", "-").replace("'", "")
    branch = "weekly-update-" + safe_theme + "-" + datetime.now().strftime("%Y-%m-%d-%H%M")

    run(["git", "stash"])
    run(["git", "checkout", BASE_BRANCH])
    run(["git", "pull", "origin", BASE_BRANCH])
    run(["git", "checkout", "-b", branch])
    run(["git", "stash", "pop"])
    run(["git", "add", "index.html", "style.css"])
    run(["git", "commit", "-m", "Weekly ad update - " + theme + " (" + date_str + ")"])
    run(["git", "push",
         "https://" + GITHUB_TOKEN + "@github.com/" + GITHUB_REPO + ".git",
         branch])
    print("Pushed branch: " + branch)

    response = requests.post(
        "https://api.github.com/repos/" + GITHUB_REPO + "/pulls",
        headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json",
        },
        json={
            "title": "Weekly Ad Update: " + theme + " (" + date_str + ")",
            "body": (
                "Automatically generated by the FreshMart AI agents.\n\n"
                "**Weekly Theme:** " + theme + "\n"
                "**Generated on:** " + date_str + "\n\n"
                "Review and merge to publish to the live site."
            ),
            "head": branch,
            "base": BASE_BRANCH,
        },
    )

    if response.status_code != 201:
        raise RuntimeError(
            "PR creation failed: " + str(response.status_code) + " " + response.text
        )

    pr_url = response.json().get("html_url", "")
    print("Pull request opened: " + pr_url)


if __name__ == "__main__":
    main()