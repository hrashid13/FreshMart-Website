"""
FreshMart AI Agent Runner
-------------------------
Calls three Claude API agents in sequence:
  1. Idea Agent   — invents the weekly ad content (plain text)
  2. Coder Agent  — turns the ad into index.html
  3. Style Agent  — produces style.css themed to match the week's ad

Before running the Idea Agent, fetches the last merged PR title from GitHub
so the agent knows what theme was used last week and avoids repeating it.

Then hands off to publish.py which pushes a branch and opens a PR on GitHub.
You review the PR and decide whether to merge. That is the only human step.

Modes:
  python runner.py            → infinite loop, fires every Monday 08:00
  python runner.py --run-now  → runs the job once immediately and exits
                                (used by GitHub Actions)
"""

import logging
import os
import re
import sys
import time

import anthropic
import requests
import schedule

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Claude model
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Agent prompts
# ---------------------------------------------------------------------------

IDEA_AGENT_PROMPT = """\
You are a copywriter. You only write plain text. No HTML. No code. No JSON.

Write a weekly ad for FreshMart grocery store using EXACTLY this format
and nothing else. Do not add any explanation before or after.

THEME: [pick a fresh, seasonal, or fun theme — never reuse a recent one]

DEALS:
1. Whole Milk - was $3.99, now $2.99, 25% off
2. Chicken Breast - was $7.99, now $5.99, 25% off
3. Strawberries - was $4.99, now $2.99, 40% off
4. Cheddar Cheese - was $5.49, now $3.99, 27% off
5. Sourdough Bread - was $3.99, now $2.49, 38% off

RECIPE:
Name: Summer Berry Smoothie
Ingredients: strawberries, banana, yogurt, honey, ice
Steps:
1. Add all ingredients to a blender.
2. Blend until smooth.
3. Pour into a glass and serve cold.

ANNOUNCEMENT:
FreshMart is open Monday to Saturday 8am to 9pm and Sunday 9am to 7pm.

Now write your OWN version following that exact format. Choose a completely
original theme appropriate for the current season. Different items, different
theme, different recipe, different announcement. Plain text only.\
"""

CODER_AGENT_PROMPT = """\
You are an HTML developer. You receive plain text weekly ad content.
Turn it into a valid, complete HTML file using EXACTLY this structure,
filling in every placeholder with real content from the ad.

<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FreshMart</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
<h1>FreshMart</h1>
<p>[THEME HERE]</p>
</header>
<section id="deals">
<div class="card"><h2>[ITEM 1 NAME]</h2><p>[ITEM 1 PRICE INFO]</p></div>
<div class="card"><h2>[ITEM 2 NAME]</h2><p>[ITEM 2 PRICE INFO]</p></div>
<div class="card"><h2>[ITEM 3 NAME]</h2><p>[ITEM 3 PRICE INFO]</p></div>
<div class="card"><h2>[ITEM 4 NAME]</h2><p>[ITEM 4 PRICE INFO]</p></div>
<div class="card"><h2>[ITEM 5 NAME]</h2><p>[ITEM 5 PRICE INFO]</p></div>
</section>
<section id="recipe">
<h2>[RECIPE NAME]</h2>
<ul>
<li>[INGREDIENT 1]</li>
<li>[INGREDIENT 2]</li>
<li>[INGREDIENT 3]</li>
</ul>
<ol>
<li>[STEP 1]</li>
<li>[STEP 2]</li>
<li>[STEP 3]</li>
</ol>
</section>
<section id="announcement">
<p>[ANNOUNCEMENT TEXT]</p>
</section>
<footer><p>FreshMart. All rights reserved.</p></footer>
</body>
</html>

Output ONLY the completed HTML. No explanation. No markdown. No code fences.
The ad content you must use is below:

"""

STYLE_AGENT_PROMPT = """\
You are a CSS developer. Write a complete style.css for the FreshMart website
that matches the theme and mood of this week's ad content.

Rules:
- Keep the same HTML structure and class names (body, header, .card, #deals, #recipe, #announcement, footer)
- Change the color scheme to fit the theme — header and footer background, card borders, accent colors
- Keep the layout and font sizes roughly the same so the site stays readable
- You can make small creative tweaks to border-radius, shadows, or spacing
- Output ONLY valid CSS. No explanation. No HTML. No markdown. No code fences.

The weekly theme is:\
"""


# ---------------------------------------------------------------------------
# GitHub helper — fetch the last weekly ad theme from merged PRs
# ---------------------------------------------------------------------------

def get_last_theme() -> str:
    """Look at the most recent closed PRs and return the last weekly theme used."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        log.warning("GITHUB_TOKEN not set — cannot fetch last theme, will skip.")
        return ""
    try:
        response = requests.get(
            "https://api.github.com/repos/hrashid13/FreshMart-Website/pulls",
            headers={
                "Authorization": "token " + token,
                "Accept": "application/vnd.github.v3+json",
            },
            params={
                "state": "closed",
                "per_page": 10,
                "sort": "updated",
                "direction": "desc",
            },
            timeout=10,
        )
        if response.status_code != 200:
            log.warning("GitHub PR fetch returned " + str(response.status_code) + " — skipping last theme check.")
            return ""
        for pr in response.json():
            title = pr.get("title", "")
            if "Weekly Ad Update" in title:
                # Title format: "Weekly Ad Update: Harvest Festival (2026-03-30)"
                match = re.search(r'Weekly Ad Update:\s*(.+?)\s*\(', title)
                if match:
                    return match.group(1).strip()
    except Exception as e:
        log.warning("Could not fetch last theme: " + str(e))
    return ""


# ---------------------------------------------------------------------------
# Claude API helper
# ---------------------------------------------------------------------------

def call_claude(system_prompt: str, user_message: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_html(text: str) -> str:
    match = re.search(r'(<!DOCTYPE html>.*?</html>)', text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r'(<html.*?</html>)', text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    if '<header>' in text or '<section' in text:
        return text.strip()
    raise ValueError("No valid HTML found in Coder Agent output.")


def extract_theme(text: str) -> str:
    match = re.search(r'THEME[:\s]+([^\n]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "Weekly Update"


# ---------------------------------------------------------------------------
# The weekly job
# ---------------------------------------------------------------------------

def weekly_job():
    log.info("=" * 60)
    log.info("Weekly job started")

    try:
        # Check what theme was used last week and tell the Idea Agent to avoid it
        last_theme = get_last_theme()
        if last_theme:
            log.info("Last theme was: " + last_theme + " — instructing agent to avoid it")
            user_message = (
                "Do NOT use this theme or anything similar to it: " + last_theme + "\n\n"
                "Generate this week's FreshMart ad with a completely different theme."
            )
        else:
            log.info("No previous theme found — agent will pick freely")
            user_message = "Generate this week's FreshMart ad."

        log.info("Running Idea Agent...")
        idea_text = call_claude(IDEA_AGENT_PROMPT, user_message)
        theme = extract_theme(idea_text)
        log.info("Idea Agent done. Theme: " + theme)

        log.info("Running Coder Agent...")
        html_raw = call_claude(CODER_AGENT_PROMPT, idea_text)
        html = extract_html(html_raw)
        log.info("Coder Agent done. HTML: " + str(len(html)) + " chars")

        log.info("Running Style Agent...")
        css = call_claude(STYLE_AGENT_PROMPT, theme)
        log.info("Style Agent done. CSS: " + str(len(css)) + " chars")

        import publish
        publish.main(html=html, css=css, theme=theme)

        log.info("Weekly job complete.")

    except Exception as e:
        log.error("Weekly job FAILED: " + str(e), exc_info=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--run-now" in sys.argv:
        # GitHub Actions mode: run once and exit
        weekly_job()
    else:
        # Persistent mode (Pi or local): loop forever
        log.info("Scheduler started. Next run: Monday 08:00")
        schedule.every().monday.at("08:00").do(weekly_job)
        while True:
            schedule.run_pending()
            time.sleep(30)
