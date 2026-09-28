# Hosted demo (Vercel)

A static page that shows only the **Workspace**: the office where each agent is a robot. It plays a made-up day in a loop, entirely in the browser. There is no server, it makes no network requests after loading, and it contains none of your real data (the companies and events are fictional).

## What is in this folder

| File | What it is |
|---|---|
| `index.html` | The page: the office, a live-style activity feed, and a "who's who" legend |
| `demo.js` | Plays the made-up day and feeds the scene its events |
| `workspace.js`, `workspace.css` | The office and robots. **Copies** of `../dashboard/workspace.*` |
| `sync.py` | Refreshes those two copies (`--check` reports if they are out of date) |
| `vercel.json` | Security headers |

`workspace.js` and `workspace.css` are copied here because Vercel only deploys what is inside this folder. After changing the scene in `dashboard/`, run:

```
python site/sync.py
```

and commit the result. `python site/sync.py --check` exits with an error if the copies are stale.

## Try it locally

```
cd site
python -m http.server 8000
```

Open http://localhost:8000/. Add `?theme=dark` or `?theme=light` to pick a theme.

## Deploy to Vercel

1. Install the CLI once: `npm i -g vercel`
2. From this folder: `cd site` then `vercel` and answer the prompts. Framework: **Other**. Build command: none. Output directory: `.`
3. Publish to production with `vercel --prod`.

Or in the Vercel dashboard: import the GitHub repository and set **Root Directory** to `site`. Leave the build settings empty.

## What this is not

It is not connected to your real tracker, emails or Telegram. To show your real agents, the live dashboard (`python dashboard.py`) runs on your own computer; putting that on the public internet would expose company names and email activity, so it is deliberately not part of this site.
