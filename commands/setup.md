---
description: Connect telegram-relay to your Telegram bot
argument-hint: <bot token from @BotFather>
---
Connect this plugin to the user's Telegram bot. The token is: $ARGUMENTS

1. If no token was given, tell the user to open Telegram, send `/newbot` to @BotFather, and run `/telegram-relay:setup <token>`. Stop there.
2. Run: `printf '%s\n' '<token>' | sh "${CLAUDE_PLUGIN_ROOT}/run.sh" setup -` (the token goes through stdin so it never shows in the process list). It prints an 8-character code, valid for 10 minutes, and the bot's username.
3. Tell the user to send that code to the bot in a private chat, and wait until they say they did.
4. Run: `sh "${CLAUDE_PLUGIN_ROOT}/run.sh" pair --no-install`. The plugin already provides the hooks, so `--no-install` is required.
5. Report the result. If it worked, they get a "Connected" message in Telegram, and sessions started from now on are covered. Bot commands: `/projects`, `/sessions @project`, `/status`, `/away`, `/back`, `/mute <project>`, `/unmute <project>`.
