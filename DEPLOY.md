# Running it on an always-on host

The Telegram poller and the scheduler are long-running processes that keep their state in files, so they need a host that stays up and has a persistent disk. Vercel and other serverless platforms don't fit. This works on Railway, Render, Fly.io or any VPS that can run a Docker image.

`Dockerfile` builds the image and `run_all.py` runs the poller and the scheduler together, restarting either one if it stops. The dashboard is not started: it has no login, and it shows your applications.

## Before you start

- **Keep the repo private.** It contains your candidate profile, your tracker and your Telegram chat ID.
- **Stop the copies on your PC** (close the Findjobs windows). Two pollers on one bot token fight over Telegram messages, and two schedulers run every job twice.

## Steps (Railway)

1. New project, then Deploy from GitHub repo, and pick this repo. Railway finds the `Dockerfile`.
2. Add a **Volume** and mount it at `/app/data`. Everything that changes lives there: the tracker, notifications, `config.json` (your mode and pause changes), run outputs and logs. On the first start it is filled from the copy baked into the image, so your history carries over.
3. Under Variables, add the same values you have set on your PC:

   | Variable | Needed for |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | the bot |
   | `LLM_API_KEY` | Gemini (or `ANTHROPIC_API_KEY`) |
   | `TAVILY_API_KEY` or `BRAVE_API_KEY` | web search |
   | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | sending emails after your `send <id>` |
   | `LLM_MODEL`, `LLM_BASE_URL`, `LLM_FALLBACK_MODELS` | only if you override the defaults |

4. Keep it at **one replica**. Deploy, then watch the logs for `[run_all] started poller` and `started scheduler`.

The scheduler uses `TIMEZONE` from `config.json` (`Asia/Colombo`), so the 11:00 daily search runs at 11:00 your time whatever the server's clock says. It catches up on a missed job when it starts.

## Things to know

- **The tracker lives on the volume, not in git.** After you deploy, the copy on the host is the real one. Redeploys keep it. Your local `data/` stops being updated.
- **Manual submit packs:** the Telegram message includes the cover letter itself, so you can copy it from your phone. The full pack is also saved on the volume as `data/manual_packs/<id>.md`.
- **Secrets stay out of git.** Set them as variables on the host only.
- To look at the dashboard, run it on your PC against a copy of the volume's data. Don't expose it publicly.
