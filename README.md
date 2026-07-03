# Daily News → Telegram

A tiny bot that pulls topic-based headlines from RSS (deduped, last 24h) and
sends them to a Telegram chat once a day, run free on GitHub Actions.

## One-time setup (~5 min)

### 1. Create the bot
1. In Telegram, message **@BotFather** → `/newbot` → follow prompts.
2. Copy the **bot token** it gives you (looks like `123456:ABC-DEF...`).

### 2. Get your chat ID
1. Send any message to your new bot (search its username, tap Start, say "hi").
2. Open this URL in a browser, pasting your token:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":123456789` — that number is your **chat ID**.
   *(Shortcut: message **@userinfobot**, which replies with your ID.)*

### 3. Put this code on GitHub
Create a new repo and push these files, keeping the folder structure:
```
news_bot.py
requirements.txt
.github/workflows/daily-news.yml
```

### 4. Add your secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add two:
- `TELEGRAM_BOT_TOKEN` → your bot token
- `TELEGRAM_CHAT_ID` → your chat ID

### 5. Test it
Go to the **Actions** tab → **Daily News** → **Run workflow**. You should get a
message within a minute. After that it runs automatically every day at 07:00 UTC.

## Customising
- **Topics & sources:** edit the `TOPICS` dict in `news_bot.py`. Add any RSS feed
  URL, or use `google_news("your query when:1d")` to pull many outlets per topic.
- **Region/language:** change `hl`, `gl`, `ceid` in the `google_news()` function
  (e.g. `en-GB` / `GB` / `GB:en`).
- **Time of day:** edit the `cron` line in the workflow (UTC). https://crontab.guru
- **How many headlines:** tweak `ITEMS_PER_TOPIC` and `MAX_AGE_HOURS`.

## Send to a group or channel instead
Add the bot to the group/channel as an admin, then use the group/channel chat ID
(groups are negative numbers, e.g. `-1001234567890`) as `TELEGRAM_CHAT_ID`.
