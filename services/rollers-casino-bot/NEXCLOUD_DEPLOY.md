# NexCloud VPS deployment

## Why the server was going offline

The NexCloud startup template was receiving stale package names through its
`PY_PACKAGES` variable. Those names are not dependencies for this bot, so pip
failed before the bot could start.

This project now installs only the packages listed in `requirements.txt`.

## Startup command

After uploading and extracting the project, set the NexCloud/Pterodactyl
startup command to:

```text
bash nexcloud_start.sh
```

If the panel starts from `/home/container` but the project is in a
subdirectory, use the script's full path instead:

```text
bash /path/to/your/project/nexcloud_start.sh
```

Do not use a bare `python main.py` command from a different directory. The
launcher has verified that `main.py` is the bot's real entry point and resolves
the project directory from the launch script itself.

Also clear the server's `PY_PACKAGES` variable.

The launcher will:

1. Use Python 3.11 or newer.
2. Create a local `.venv`.
3. Install the exact dependencies from `requirements.txt`.
4. Verify the required imports.
5. Start the verified bot entry point, `main.py`.

## Port and 404 checks

The public Flask server binds to the `PORT` environment variable first, then
uses local-development fallbacks only when that variable is absent or invalid.
Configure the VPS panel's public/internal application port in `PORT` and proxy
that same port. The liveness URL is:

```text
GET /healthz
```

It returns HTTP 200 with a small JSON response once the Telegram token is
configured and the bot process has started. A 404 from another port means the
panel or reverse proxy is pointing at a different process.

## Required environment variable

Set this in the NexCloud server variables panel:

```text
TELEGRAM_BOT_TOKEN=<token from @BotFather>
```

Do not commit the token to the repository or put it in a public file.

Optional variables used by payment and dealer-bot features are documented in
`bot.env.example`. `SERVER_URL` should be the public HTTPS URL if payment
webhooks are enabled.

## Manual VPS alternative

From the project directory:

```bash
bash vps_start.sh
```

The script intentionally ignores any inherited `PY_PACKAGES` value so stale
panel settings cannot break dependency installation.