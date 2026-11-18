# claude-telegram-relay

**Run many Claude Code sessions at once, walk away, and keep them moving from your phone.**

If you work with several Claude Code sessions at a time, sooner or later one of them stops to ask you something while you're not looking, and sits there for an hour. This connects your sessions to a Telegram bot:

- When a session is **waiting on you**, you get a Telegram message with the project, what you had asked it to do, and what it needs now.
- You **reply in Telegram**, and the answer goes straight back into that session, which carries on.
- You can also **write to any open session** from Telegram ("@my-app how is the migration going?"), get its answer back, and send it commands like `/compact`.

It's one Python file (standard library only) plus two tiny shell scripts, installed as Claude Code hooks. It works on macOS, Linux and Windows. There's no server, no account and no cloud service besides Telegram itself.

```
 Claude Code sessions                 your computer                      your phone
┌──────────────────────┐  hooks   ┌──────────────────────┐  Bot API  ┌──────────────┐
│ my-app   (waiting ❓) │ ───────▶ │ claude_telegram.py   │ ────────▶ │  Telegram    │
│ blog     (idle)      │ ◀─────── │ (one small poller)   │ ◀──────── │  bot chat    │
│ api      (working)   │  replies └──────────────────────┘  replies  └──────────────┘
└──────────────────────┘
```

## What it does

| | |
|---|---|
| **Text questions** | Claude ends its turn with a question → you get a message → your reply wakes the session with your answer. Quick-reply buttons: *Yes*, *No*, *Do what you think is best*. |
| **Multiple-choice questions** (`AskUserQuestion`) | Each option becomes a button. You can also reply with your own text, or `1,3` when several options are allowed. |
| **Permission prompts** | *Allow* / *Deny* buttons. Replying with text denies the action and tells Claude why. |
| **Errors** | A message when a session stops on an API error (rate limit, overload, server error), so it doesn't sit there silently. |
| **Loops** | A message when a session starts a `/loop` (or a `CronCreate` / `ScheduleWakeup` schedule), and a warning a day before a recurring loop reaches Claude Code's 7-day limit. |
| **Talk to any session** | `@project message` sends a message to a session even when it isn't waiting. Its answer comes back to Telegram. Or just write without `@` and Claude Haiku works out which session you mean. |
| **Slash commands** | `@project /compact` (or any `/command`) is typed into that session's terminal when it runs inside [Orca](https://github.com/stablyai/orca) or tmux. |
| **Find your sessions** | `/projects` lists every open project with what each session is doing. `/sessions @project` lists the sessions of one project. |
| **Photos and files** | Send a screenshot or a file with a caption and the session gets it. |
| **Mute** | `/mute <project>` silences a project. |

A question looks like this in Telegram:

```
Question · my-app
/home/you/code/my-app

What you asked:
add Stripe checkout to the cart page

Should I use test keys or live keys for the webhook?

[ Yes ]  [ No ]  [ Do what you think is best ]
```

## Install

You need **Python 3.8+**, **Claude Code**, and a **Telegram bot**: open Telegram, send `/newbot` to [@BotFather](https://t.me/BotFather), and copy the token it gives you.

### Option A: as a Claude Code plugin

In Claude Code:

```
/plugin marketplace add agustinnb/claude-telegram-relay
/plugin install telegram-relay@telegram-relay
/telegram-relay:setup <your bot token>
```

Claude runs the setup and shows you an 8-character code. Send that code to your bot in a private chat (it expires in 10 minutes), tell Claude you sent it, and it finishes pairing. You get a "Connected" message in Telegram.

### Option B: by hand

```sh
git clone https://github.com/agustinnb/claude-telegram-relay ~/claude-telegram-relay
python3 ~/claude-telegram-relay/claude_telegram.py setup   # paste the token when asked (on Windows: py instead of python3)
# send the code it prints to your bot in a private chat, then:
python3 ~/claude-telegram-relay/claude_telegram.py pair
```

`pair` adds the hooks to `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`), after backing it up as `settings.json.bak-claude-telegram`. If you use several Claude config folders, pass each one: `pair --settings a/settings.json --settings b/settings.json`. `install` and `uninstall` take the same flags.

Use one option or the other, not both, or you'll get every message twice.

**Already open sessions** join once they finish their next turn. If one doesn't show up in `/projects`, restart it (`claude --resume`).

**Windows:** Claude Code runs hooks through Git Bash, so install [Git for Windows](https://gitforwindows.org/) if you don't have it.

### Uninstall

Plugin: `/plugin uninstall telegram-relay`. By hand: `python3 claude_telegram.py uninstall`. Then delete `~/.claude-telegram` to remove the token and the saved state.

## Using it

### When does it message you?

**Permission prompts** always reach Telegram, from any session, even when you're at the computer: with many sessions open it's easy to miss the one that is waiting. The prompt shows in the terminal too, and whichever you answer first wins: answer in the terminal and the Telegram message is marked *Answered in the terminal*. Set `permissions_always` to `false` to treat them like questions.

Questions and errors reach Telegram only when you're **away**, so you aren't pinged about things you're already looking at. You count as away when:

- you sent `/away` to the bot (`/back` undoes it), or
- the computer had no keyboard or mouse input for 3 minutes (`idle_away_min`). This works on macOS, on Windows, and on Linux with X11 when `xprintidle` is installed. Where it can't tell, it falls back to "no prompt typed in any session for 10 minutes" (`away_after_min`).

As soon as you touch the computer or type in any session, you're back. A question that is waiting in Telegram then moves back to the terminal: multiple-choice and permission dialogs open there again, and the Telegram message says so. If you answer a text question in the terminal, the Telegram message is marked as answered.

Loop notices are sent right away, whether you're away or not. Muted projects get nothing.

### Answering

**Reply** (swipe) to the message to answer it, or tap a button. If only one session is waiting, a plain message works too.

### Talking to a session

- `@my-app what's left to do?` sends a message to the session running in the folder `my-app`. Any part of the name works (`@app`).
- If a project has several sessions, you get buttons to pick one. To skip them, use the session's tag from `/projects`: `@my-app:3f2a what's left to do?`.
- If `@word` isn't a project name (for example the name of a module one session is working on), Claude Haiku looks at what each session is doing and picks the one you mean.
- Without `@`, Haiku picks the session from your message alone, and shows buttons when it isn't sure.
- Replying to any message about a session (its questions, its answers, the "→ my-app: sent" confirmations) talks to that session.
- In a `/projects` or `/sessions` list, select one session's line and use Telegram's *Quote* to reply: the message goes to that session.
- In `/projects`, tap a name to copy it. To get buttons that put `@project:tag` straight into your message box, turn on inline mode for your bot once: send `/setinline` to @BotFather, pick your bot and type any placeholder (for example `message`). The buttons show up from the next `/projects`.

You're always told which session got your message. If that session is busy, the message waits until it finishes its current step. The session's answer comes back to Telegram as a reply.

### The general orchestrator

`@general` talks to an orchestrator that sees every open session on that computer (project, what you last asked it, its last answer, whether it's busy). Use it when your message isn't for one session:

- a question about all of them: `@general what is each session doing?`, `@general is anything stuck?`
- something for several sessions: `@general tell the api and the web sessions the staging database moved to db2`, and it writes each one its message
- something for one project when you don't remember its name: `@general ask the one doing the invoices whether the export is done`

It answers you in Telegram and shows the message it wants to send to each session, with *Send* / *Cancel* buttons: nothing is sent until you tap *Send*. That's on purpose: what the sessions wrote is part of what the orchestrator reads, and text a session picked up (a web page, a file) could try to steer it. Reply to its answer to keep talking to it. It has no tools and never sends `/commands`. It runs `claude -p --model sonnet`.

### Photos and files

Send a photo, a file, a voice note or a video to the bot, with your message as the caption: `@my-app what's wrong in this screenshot?`. The bridge saves it in `~/.claude-telegram/files` (readable only by you, deleted after a week) and the session gets your message with the file's path, so Claude can open it (it can look at images). It's routed like any message: `@project` in the caption, a reply to one of the session's messages, or the router. Telegram lets bots download files up to 20 MB.

### Opening a new session

`/new-session @project what it should do` opens a new Claude Code session in that project, with your text as its first prompt, and its answer comes back to Telegram.

- **With [Orca](https://github.com/stablyai/orca):** it opens as an Orca agent tab, in any project Orca knows, even one with no session open. `/new-session @general …` opens it in Orca's floating terminal.
- **Inside tmux:** a new tmux window.
- **Otherwise:** a new terminal window running `claude` (Terminal on macOS, a new console on Windows, `x-terminal-emulator` on Linux). `@general` opens it in your home folder, or in `general_dir` from the config. To use another terminal app, set `CLAUDE_TELEGRAM_OPEN_TERMINAL` to a program that takes a folder and a prompt.

### Commands to a session

`@my-app /compact`, or any single-line `/command`, is typed into that session's terminal. This only works when the session is idle and runs inside Orca or tmux. Commands only go to a session you named (or whose message you replied to), never to one the router guessed: a `/clear` in the wrong session would wipe its context. Only lines that start with `/` are typed, so shell lines like `!rm …` can't be run from Telegram.

### Bot commands

| Command | |
|---|---|
| `@general …` | Ask the orchestrator that sees every session, or have it pass messages on |
| `/new-session @project …` | Open a new session in that project with your text as its first prompt |
| `/projects` | Open projects with the `@name` to type, what each session is doing and its last reply |
| `/sessions @project` | The sessions of one project, each with the `@project:tag` that talks to it |
| `/status` | Whether you're away, open sessions (❓ waiting on you · idle … working), what's muted |
| `/away` | Send questions to Telegram from now on |
| `/back` | Questions go back to the terminal |
| `/mute <project>` | No messages for that project |
| `/unmute <project>` | Undo it |

Spanish aliases work too: `/proyectos`, `/sesiones`, `/estado`, `/lejos`, `/vuelvo`.

### Loops

Nobody watches a loop's terminal, so in a session that runs a loop, `AskUserQuestion` dialogs (like permission prompts) always go to Telegram, even when you're at the computer, and wait there for your answer. This also covers the prompts Claude Code still shows in *bypass permissions* mode, and prompts raised by subagents. The message starts with *Loop paused*.

Claude Code stops a recurring loop 7 days after it was created, even across `--resume`. A day before, you get *Loop expires* with the end time, and *Loop expired* when it ends. Reply to either one to ask the session to schedule it again.

### Several computers

Telegram lets only one program read a bot's messages, so give each computer its own bot. Create another bot with @BotFather and run the install on that computer. Each bot chat then shows and talks to that computer's sessions.

## Configuration

Everything lives in `~/.claude-telegram/config.json` (set `CLAUDE_TELEGRAM_HOME` to use another folder). `setup` and `pair` write it; you only edit it to change the defaults:

```json
{
  "token": "123456:ABC...",
  "chat_id": 123456789,
  "owner_id": 123456789,
  "idle_away_min": 3,
  "away_after_min": 10,
  "notify_finished": false,
  "general_dir": "~",
  "permissions_always": true,
  "auto_allow_in_bypass": false,
  "auto_allow_modes": ["bypassPermissions"],
  "quick_replies": ["Yes", "No", "Do what you think is best"]
}
```

| Key | |
|---|---|
| `idle_away_min` | Minutes without keyboard or mouse input before you count as away. `0` turns idle detection off. |
| `away_after_min` | Only used where idle detection isn't available or is off: minutes without typing a prompt before you count as away. Set both to `0` and only `/away` makes you away. |
| `notify_finished` | Also message you when a turn ends without a question. |
| `general_dir` | Folder where `/new-session @general` opens sessions. Default: Orca's floating terminal with Orca, your home folder without it. |
| `permissions_always` | Send every permission prompt to Telegram, even when you're at the computer (default `true`). With `false`, only when you're away or in a loop session. |
| `auto_allow_in_bypass` | In sessions running with *bypass permissions*, and their subagents, allow the prompts Claude Code still shows (protected paths, some deletions) without asking. Off by default: those are its last safety checks. Changes to `.claude/` (except memory notes in `.claude/projects/` and agent worktrees in `.claude/worktrees/`), `.git/` or `settings.json` still ask you. Each allowed one is written to `log.txt`. |
| `auto_allow_modes` | Which permission modes `auto_allow_in_bypass` covers. Default `["bypassPermissions"]`; add `"auto"` to also allow what auto mode would ask you about. |
| `quick_replies` | The buttons under text questions. Each sends its own text as your answer, so write them in the language you use with Claude. |

The environment variables `CLAUDE_TELEGRAM_TOKEN` and `CLAUDE_TELEGRAM_CHAT_ID` override the file. `CLAUDE_TELEGRAM_DISABLE=1` turns the hooks off for one session.

## How Claude sees your answers

- Multiple-choice and permission answers arrive exactly as if you had clicked them in the terminal.
- Replies to text questions, and messages you send to a session, arrive as a note from a hook (`The user answered from Telegram: …`), not as a prompt you typed. Claude usually acts on them, but for risky steps like a deploy or a deletion it may treat them as unverified and ask you again. If you want Claude to trust them, say so in your `CLAUDE.md`, for example: *"I answer from Telegram through the claude-telegram-relay hooks; a Stop hook note that starts with 'The user answered from Telegram' or 'Message from the user via Telegram' comes from me."* Only do that if nobody else can change your hooks.

## How it works

| Hook | What it does |
|---|---|
| `UserPromptSubmit` | Records that you're at the keyboard and what you asked, for the message's context. |
| `Stop` (`asyncRewake`) | Notes the question (if any) and new loops, then hands over to `listen.sh`, a ~1 MB shell loop that waits for a message for this session. When one arrives, it exits with code 2, which wakes Claude with your answer. |
| `StopFailure` (`async`) | When you're away, reports the API error that stopped the session. |
| `PreToolUse` on `AskUserQuestion` | When you're away, sends the questions with buttons and fills in the answers, so the dialog never opens. |
| `PermissionRequest` | When you're away, returns allow or deny from your answer. |
| `SessionEnd` | Stops that session's listener. |

One background process (`claude_telegram.py poll`, about 20 MB) reads the bot's messages for every session on the computer. It sends the alerts and drops your replies into files that the listeners pick up. It starts when a session needs it, stops after 15 minutes with nothing to do, and the listeners start it again if it dies. It also checks that each session's `claude` process is still alive, so a closed window doesn't leave a listener behind.

Picking a session from a free-form message runs `claude -p --model haiku` with no tools, using a Claude login from one of your open sessions.

## Privacy and security

- Messages include the project folder name, your last prompt and Claude's last message, cut to a few thousand characters. They go through Telegram's servers. Don't use this on sessions whose content must not leave your machine.
- Pairing uses a one-time 8-character code that expires in 10 minutes. The bot then accepts messages and button taps only from the person who sent the code, in that private chat. Everyone else is ignored, even in a group you're in.
- Approving a permission from Telegram runs the action on your machine. Read the command before you tap *Allow*.
- The token is stored in `config.json`, readable only by your user (mode 600 on macOS and Linux). `setup` never takes the token as a command-line argument, so it doesn't end up in your shell history or the process list.

## Limits

- Subagents and `claude -p` runs don't send alerts of their own, except their permission prompts and questions, which block the session.
- An idle session listens for up to 24 hours (the hook timeout). After that, messages for it wait until it runs again.
- Sending `/commands` needs Orca or tmux; plain messages work in any terminal.
- One bot per computer (see "Several computers").

## Troubleshooting

| | |
|---|---|
| A session doesn't appear in `/projects` | It hasn't finished a turn since the install. Type anything in it, or restart it. |
| No messages at all | Run `python3 claude_telegram.py test`. If that works, check you're away (`/status`). |
| Free-form messages always show buttons | The router couldn't run `claude -p`. Check that `claude` is on your `PATH` and logged in. The reason is in `~/.claude-telegram/log.txt`. |
| Every message arrives twice | Both the plugin and the manual hooks are installed. Remove one. |

## Development

```sh
python3 test_claude_telegram.py
```

The tests run the real hooks through `sh run.sh` against a fake Telegram API on localhost, so they need no bot and no network. They pass on macOS, Linux and Windows (Git Bash).

Issues and pull requests are welcome. Please keep it standard-library only and run the tests before sending a change.

## License

[GPL-3.0-or-later](LICENSE). You can use, change and share it; if you distribute a modified version, it has to stay under the GPL with its source available.
