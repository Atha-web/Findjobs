# Job Application Agent System: Build Guide

Prompts and specs for a three-agent job application system with a shared tracker and Telegram updates.

## Files

| File | What it is |
|---|---|
| `00_shared_rules.md` | Rules every agent follows. Goes at the top of every agent's system prompt. |
| `01_research_agent.md` | Agent 1: finds, checks and scores jobs. Never applies. |
| `02_application_agent.md` | Agent 2: prepares and submits applications, writes emails and follow-ups. |
| `03_tracking_agent.md` | Agent 3: reads your Telegram replies and classifies recruiter emails. |
| `04_candidate_profile_template.md` | Your facts, preferences and standard answers. Fill in once. |

## Assembling each agent

System prompt = `00_shared_rules.md` + the agent's own file.

At runtime, pass these in, wrapped in the tags shown:

- `<candidate_profile>`: your filled-in profile
- `<config>`: the config block below
- `<today>`: current date, weekday and timezone
- the task input described in each agent's file

Anything in `{{DOUBLE_BRACES}}` is a placeholder for you to fill in.

## How work flows

```
Scheduler (daily)         -> Agent 1 -> tracker: Discovered
Notifier (code)           -> Telegram: approval_request
You reply "1"             -> Agent 3 -> tracker: Ready to Apply
Apply queue (code)        -> Agent 2 -> tracker: Applied, or Application Started + manual pack
Notifier (code)           -> Telegram: application_submitted / manual_submit
New email in inbox        -> Agent 3 (email mode) -> tracker update -> Telegram: status_changed
Your Telegram message     -> Agent 3 (message mode) -> tracker update -> reply
Scheduler (daily)         -> follow-up check -> Agent 2 drafts -> you approve -> sent
Scheduler (daily/weekly)  -> summary from tracker counts -> Telegram
```

The agents never talk to each other directly. They read and write the tracker, and code moves work between them based on status.

## Code vs. LLM

Use the LLM for judgment and language: planning searches, extracting job details, scoring, writing, understanding your replies and classifying emails.

Use plain code for everything mechanical: generating Application IDs, duplicate lookups, rate limits, the mode switch, sending Telegram notifications, scheduling, summary counts and the history log.

Enforce the hard limits in code as well as in the prompts: daily caps, MODE, PAUSED, the allowed Telegram chat ID, and never deleting records. The prompts tell the agents the rules; code makes sure the rules hold even when a model gets something wrong.

What the code enforces today:
- **Tracker (`tracker.py`):** no deletes, no backward status moves, no duplicate fingerprints, `MAX_NEW_MATCHES_PER_DAY`, `MATCH_THRESHOLD_RECOMMEND`, `MAX_POSTING_AGE_DAYS`, `MAX_APPLICATIONS_PER_DAY`, `MAX_APPS_PER_COMPANY_30_DAYS` and `MAX_FOLLOW_UPS`. A file lock stops the poller and scheduled runs overwriting each other.
- **Sending (`tools.py`):** every email needs your `send <id>`. Code also checks `PAUSED`, `MAX_EMAILS_PER_DAY` (in your timezone), the application caps, that the application isn't already sent, and that the recipient matches the recruiter email on file.
- **Settings (`poll_telegram.py`):** the model can pause the system or switch to APPROVAL, but only you typing `CONFIRM AUTO` or `CONFIRM RESUME` can switch to AUTO or resume.
- **Left to the agents:** `EXPERIENCE_GAP_MAX_YEARS` and `MAX_FORM_PAGES`, because there is no structured field to check them against.

## Tools to implement

| Tool | Used by | Does |
|---|---|---|
| `tracker_search(filters)` | All | Find records by ID, fingerprint, URL, company, role or status |
| `tracker_get(application_id)` | All | Read one record with its history |
| `tracker_upsert(record)` | All | Create or update. Code assigns IDs, appends history, refuses deletes |
| `web_search(query)` | 1 | Search the web |
| `fetch_page(url)` | 1, 2 | Return a page's text |
| `get_resume(version)` | 2 | Return the resume file for a version |
| `email_send(to, subject, body, attachments, thread_id?)` | 2 | Send an email. Code checks caps |
| `email_draft(...)` | 2 | Save a draft without sending |
| `browser_fill(url, fields, files)` | 2 | Optional. Fill and submit a form. Without it, Agent 2 produces manual packs |

Agent 3 has only the tracker tools. It returns replies and notifications as output, and code sends them.

## Config

```
MODE=APPROVAL                    # APPROVAL or AUTO. Only you can switch, by Telegram, with confirmation.
PAUSED=false
TIMEZONE={{IANA timezone name}}  # e.g. Asia/Colombo

NOTIFY_CHANNEL=telegram
TELEGRAM_CHAT_ID={{your chat ID}}   # only this chat is accepted. See "Telegram setup".
                                    # The bot token is NOT stored here: set TELEGRAM_BOT_TOKEN as an env var.

MATCH_THRESHOLD_RECOMMEND=55     # below this: Skipped
MATCH_THRESHOLD_AUTO=80          # AUTO mode applies only at or above this
MAX_POSTING_AGE_DAYS=30
EXPERIENCE_GAP_MAX_YEARS=2
MAX_NEW_MATCHES_PER_DAY=10

MAX_APPLICATIONS_PER_DAY=5
MAX_EMAILS_PER_DAY=5
MAX_APPS_PER_COMPANY_30_DAYS=2
MAX_FORM_PAGES=4

FOLLOW_UP_AFTER_BUSINESS_DAYS=10
MAX_FOLLOW_UPS=1
NO_RESPONSE_AFTER_DAYS=30

DAILY_SEARCH_TIME=08:00
APPLY_QUEUE_EVERY_MINUTES=30
FOLLOW_UP_CHECK_TIME=10:00       # also when "No Response" is checked
DAILY_SUMMARY_TIME=19:00
WEEKLY_SUMMARY_DAY=Sunday        # this day's summary covers the week and replaces the daily one

EMAIL_SENDING_ENABLED=true       # sends only after you reply "send <id>"
BROWSER_FILL_ENABLED=false
```

These numbers are starting points. Adjust them after the first couple of weeks.

## Scheduled jobs (code)

**Daily search.** Run Agent 1. In APPROVAL mode, send an `approval_request` for each new Discovered job, highest score first, and set it to Awaiting Approval. In AUTO mode, move jobs scoring at least MATCH_THRESHOLD_AUTO with no approval reasons straight to Ready to Apply, and send the rest for approval.

**Apply queue.** Run Agent 2 on Ready to Apply records, respecting the caps.

**Follow-up check.** For Applied records older than FOLLOW_UP_AFTER_BUSINESS_DAYS with a verified recruiter email and fewer than MAX_FOLLOW_UPS follow-ups sent, set `follow_up_due` and run Agent 2 with task `follow_up`. Send you the draft for approval.

**No response.** Applied records with no employer reply after NO_RESPONSE_AFTER_DAYS become No Response.

**Summaries.** Build them from tracker counts. Include only rejection reasons that were recorded from employer emails.

## Running it

Double-click or run `start_agents.ps1`. It opens three windows:

- **Findjobs: Telegram** runs `poll_telegram.py`, which answers your messages.
- **Findjobs: Scheduler** runs `scheduler.py`, which does the daily jobs.
- **Findjobs: Dashboard** runs `dashboard.py` and opens the live dashboard at http://127.0.0.1:8765/.

The Telegram and Scheduler windows must stay open. They load your saved environment variables themselves. The dashboard is optional.

The scheduler uses the times in `config.json` and its `TIMEZONE`. If the computer was off at a job's time, the job runs when the scheduler next starts that day. A failed job retries every 30 minutes, up to 3 times a day, then messages you. `PAUSED` stops the search, apply and follow-up jobs.

```
python scheduler.py --status           # when each job last ran and whether it is due
python scheduler.py --run daily_search # run one job now
python scheduler.py --once             # run whatever is due, then exit
```

Jobs: `daily_search`, `apply_queue`, `follow_up_check`, `no_response`, `daily_summary`, `weekly_summary`.

### Live dashboard

`python dashboard.py` (or the third window from `start_agents.ps1`) shows what the agents are doing as it happens:

- an animated diagram of how the scheduler, agents, tracker, mailer and Telegram connect. Each agent is a small robot: it types while working, shakes on an error, and sleeps when its process is offline. When it saves to the tracker or stages an email it walks along the arrows to that station and back, while envelopes and notes travel between stations and speech bubbles say what it is doing
- a live activity feed (every model round, tool call, job, message and email), which you can filter by agent
- one card per agent: working, idle or error, what it is doing now, and whether the Telegram listener and scheduler are online
- the application pipeline by stage, what needs you, and when each job runs next

Try it without running anything: `python dashboard.py --demo` replays a made-up day with fictional companies and never touches your real data. Add `?theme=light` or `?theme=dark` to the address to pick a theme.

It is read-only and listens on 127.0.0.1 only, so other computers can't see it. Activity is logged to `data/events.jsonl` (git-ignored). Each entry is a short line: the agent, what it did, and the application ID. Emails, cover letters and the text of your messages are not logged.

### Commands you can type in Telegram

| You type | What happens |
|---|---|
| `send <id>` | Sends a staged email (application, follow-up or withdrawal). Handled in code, no LLM. |
| `discard <id>` | Throws away a staged email. Nothing is sent, and the draft is kept in `data/outbox/discarded`. |
| `CONFIRM AUTO` | Switches to AUTO mode. Only you typing this can do it. |
| `CONFIRM RESUME` | Resumes after a pause. |
| Anything else | Agent 3 works out what you mean, such as `pending`, `rejected` or `interview Friday`. Reply to a notification to point at that application. |

### How follow-ups work

1. `follow_up_check` finds Applied records older than `FOLLOW_UP_AFTER_BUSINESS_DAYS` that have a **verified** recruiter email and fewer than `MAX_FOLLOW_UPS` follow-ups.
2. It flags them `follow_up_due` and runs Agent 2 with task `follow_up`. Agent 2 writes a short draft, and you get it on Telegram.
3. Reply `send <id>` to send it, or `discard <id>` to drop it. If you ignore it, no second draft is made.
4. After sending, `follow_ups_sent` goes up by one and the flag clears. Code refuses a send that would go over `MAX_FOLLOW_UPS`.

Withdrawal drafts work the same way, and sending one marks the application Withdrawn.

## Telegram setup

Notifications and your replies go through a Telegram bot, using Telegram's official Bot API. There are no message templates to get approved, no 24-hour reply window, and no separate business number.

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the bot token.
2. Set the token as an environment variable. It is never stored in a file:
   ```
   $env:TELEGRAM_BOT_TOKEN = "123456:ABC-..."
   ```
3. Open a chat with your new bot and send it any message, such as "hi".
4. Run `python poll_telegram.py`. With no `TELEGRAM_CHAT_ID` set, it prints your chat ID and exits.
5. Put that ID in `config.json` as `TELEGRAM_CHAT_ID`, then run `python poll_telegram.py` again. It now listens for your messages and passes each one to Agent 3 in message mode.

The agents also need an LLM key. A free one works: create a Gemini API key at https://aistudio.google.com/apikey (no card needed) and set it as `LLM_API_KEY`. That uses Gemini through its OpenAI-compatible endpoint. To use another OpenAI-compatible provider, such as Groq, also set `LLM_BASE_URL` and `LLM_MODEL`. `ANTHROPIC_API_KEY` still works if `LLM_API_KEY` isn't set.

**How replies resolve.** Every notification sent is logged as `message_id -> application_id` in `data/notifications.json`. When you use Telegram's **Reply** on a notification, the quoted `message_id` tells Agent 3 which application you mean, so a one-word reply like "rejected" or "1" resolves correctly.

**Security.** Only messages from `TELEGRAM_CHAT_ID` are processed. Anything from another chat is logged and dropped in code.

**Sending emails.** `send <id>` is handled directly in code, with no LLM involved. It dispatches the drafted email over SMTP and marks the application Applied.

### Notification formats

`notifier.py` renders these as plain text and logs each one. The variable order is fixed in code.

**approval_request**
```
New match 🔎 Score {1}/100
Role: {2}
Company: {3}
Location: {4}
Salary: {5}
Why: {6}
Gaps: {7}
ID: {8}
Reply 1 to apply, 2 to skip, 3 for details.
```

**application_submitted**
```
Application submitted ✅
Role: {1}
Company: {2}
Method: {3}
Date: {4}
ID: {5}
Reply to this message with any update, like "rejected" or "interview Friday".
```

**manual_submit**
```
Ready for you to submit 📝
Role: {1}
Company: {2}
Apply here: {3}
Answers and cover letter: {4}
Reply "done" once you've submitted.
```

**status_changed**
```
Application update 📩
{1} at {2}
New status: {3}
Reason: {4}
Reply if this is wrong.
```

**action_required**
```
Action needed ⚠️
{1} at {2}
{3}
ID: {4}
Reply here to answer.
```

**email_ready**
```
Email drafted ✉️ - ready to send
To: {1}
Subject: {2}

{3}

Reply "send {4}" to send it as-is, or tell me what to change first.
```

**daily_summary**
```
Job summary for {1}
Today: {2} found, {3} applied, {4} waiting for you, {5} skipped
Pipeline: {6} applied, {7} interviews, {8} assessments, {9} offers
Needs you: {10}
Reply "pending" to see what's waiting.
```

## Build order

1. **Tracker, profile, Agent 1, and Agent 2 with manual packs only.** Nothing is submitted automatically. Review every score and every draft for one to two weeks, and tune the profile and thresholds. This stage alone delivers most of the value.
2. **Telegram.** Approval requests, confirmations, and Agent 3 message mode.
3. **Email.** Agent 3 email mode, then automatic sending of email applications (still with approval).
4. **Optional.** Browser form-filling for simple forms, then AUTO mode.

## A note on job sites

Many job boards prohibit automated applying and scraping in their terms, and can restrict accounts that do it. Prefer company careers pages, employers' own application systems, and the job alert emails boards send you. Agent 3 routes those alert emails to Agent 1, so you get the listings without scraping the site. Check each site's terms before automating anything on it.
