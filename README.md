# Job Application Agent System: Build Guide

Prompts and specs for a three-agent job application system with a shared tracker and WhatsApp updates.

## Files

| File | What it is |
|---|---|
| `00_shared_rules.md` | Rules every agent follows. Goes at the top of every agent's system prompt. |
| `01_research_agent.md` | Agent 1: finds, checks and scores jobs. Never applies. |
| `02_application_agent.md` | Agent 2: prepares and submits applications, writes emails and follow-ups. |
| `03_tracking_agent.md` | Agent 3: reads your WhatsApp replies and classifies recruiter emails. |
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
Notifier (code)           -> WhatsApp: approval_request
You reply "1"             -> Agent 3 -> tracker: Ready to Apply
Apply queue (code)        -> Agent 2 -> tracker: Applied, or Application Started + manual pack
Notifier (code)           -> WhatsApp: application_submitted / manual_submit
New email in inbox        -> Agent 3 (email mode) -> tracker update -> WhatsApp: status_changed
Your WhatsApp message     -> Agent 3 (message mode) -> tracker update -> reply
Scheduler (daily)         -> follow-up check -> Agent 2 drafts -> you approve -> sent
Scheduler (daily/weekly)  -> summary from tracker counts -> WhatsApp
```

The agents never talk to each other directly. They read and write the tracker, and code moves work between them based on status.

## Code vs. LLM

Use the LLM for judgment and language: planning searches, extracting job details, scoring, writing, understanding your replies and classifying emails.

Use plain code for everything mechanical: generating Application IDs, duplicate lookups, rate limits, the mode switch, sending WhatsApp templates, scheduling, summary counts and the history log.

Enforce the hard limits in code as well as in the prompts: daily caps, MODE, PAUSED, the allowed WhatsApp number, and never deleting records. The prompts tell the agents the rules; code makes sure the rules hold even when a model gets something wrong.

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
MODE=APPROVAL                    # APPROVAL or AUTO. Only you can switch, by WhatsApp, with confirmation.
PAUSED=false
TIMEZONE={{IANA timezone name}}
USER_WHATSAPP_NUMBER={{+country code and number}}

MATCH_THRESHOLD_RECOMMEND=65     # below this: Skipped
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

DAILY_SUMMARY_TIME=19:00
WEEKLY_SUMMARY_DAY=Sunday
```

These numbers are starting points. Adjust them after the first couple of weeks.

## Scheduled jobs (code)

**Daily search.** Run Agent 1. In APPROVAL mode, send an `approval_request` for each new Discovered job, highest score first, and set it to Awaiting Approval. In AUTO mode, move jobs scoring at least MATCH_THRESHOLD_AUTO with no approval reasons straight to Ready to Apply, and send the rest for approval.

**Apply queue.** Run Agent 2 on Ready to Apply records, respecting the caps.

**Follow-up check.** For Applied records older than FOLLOW_UP_AFTER_BUSINESS_DAYS with a verified recruiter email and fewer than MAX_FOLLOW_UPS follow-ups sent, set `follow_up_due` and run Agent 2 with task `follow_up`. Send you the draft for approval.

**No response.** Applied records with no employer reply after NO_RESPONSE_AFTER_DAYS become No Response.

**Summaries.** Build them from tracker counts. Include only rejection reasons that were recorded from employer emails.

## WhatsApp setup

You need the WhatsApp Business Platform (Meta's Cloud API directly, or through a provider such as Twilio) and a phone number that isn't your personal WhatsApp. Your personal number is the recipient.

Messages the system sends first (approvals, confirmations, summaries) must use pre-approved message templates. Free-form replies are allowed within 24 hours of your last message. Submit the templates below for approval early, since review can take time.

Your replies arrive by webhook, including the ID of any message you quoted. Store `message_id -> application_id` for every notification sent. That mapping is what lets a one-word reply like "rejected" resolve to the right application.

Accept messages only from USER_WHATSAPP_NUMBER, and drop everything else in code.

If you aren't attached to WhatsApp, a Telegram bot has none of the template or number requirements and is much quicker to set up.

### Templates

Meta reviews every template and commonly rejects ones that start or end with a variable, so each of these ends with fixed text.

**approval_request**
```
New match 🔎 Score {{1}}/100
Role: {{2}}
Company: {{3}}
Location: {{4}}
Salary: {{5}}
Why: {{6}}
Gaps: {{7}}
ID: {{8}}
Reply 1 to apply, 2 to skip, 3 for details.
```

**application_submitted**
```
Application submitted ✅
Role: {{1}}
Company: {{2}}
Method: {{3}}
Date: {{4}}
ID: {{5}}
Reply to this message with any update, like "rejected" or "interview Friday".
```

**manual_submit**
```
Ready for you to submit 📝
Role: {{1}}
Company: {{2}}
Apply here: {{3}}
Answers and cover letter: {{4}}
Reply "done" once you've submitted.
```

**status_changed**
```
Application update 📩
{{1}} at {{2}}
New status: {{3}}
Reason: {{4}}
Reply if this is wrong.
```

**action_required**
```
Action needed ⚠️
{{1}} at {{2}}
{{3}}
ID: {{4}}
Reply here to answer.
```

**daily_summary**
```
Job summary for {{1}}
Today: {{2}} found, {{3}} applied, {{4}} waiting for you, {{5}} skipped
Pipeline: {{6}} applied, {{7}} interviews, {{8}} assessments, {{9}} offers
Needs you: {{10}}
Reply "pending" to see what's waiting.
```

## Build order

1. **Tracker, profile, Agent 1, and Agent 2 with manual packs only.** Nothing is submitted automatically. Review every score and every draft for one to two weeks, and tune the profile and thresholds. This stage alone delivers most of the value.
2. **WhatsApp.** Approval requests, confirmations, and Agent 3 message mode.
3. **Email.** Agent 3 email mode, then automatic sending of email applications (still with approval).
4. **Optional.** Browser form-filling for simple forms, then AUTO mode.

## A note on job sites

Many job boards prohibit automated applying and scraping in their terms, and can restrict accounts that do it. Prefer company careers pages, employers' own application systems, and the job alert emails boards send you. Agent 3 routes those alert emails to Agent 1, so you get the listings without scraping the site. Check each site's terms before automating anything on it.
