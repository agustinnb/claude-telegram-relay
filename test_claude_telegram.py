"""End-to-end tests: real hooks (through run.sh, as Claude Code runs them) against a fake Telegram API.
Run: python3 test_claude_telegram.py   (Windows: needs Git for Windows' sh)"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "claude_telegram.py"
RUN = HERE / "run.sh"
SH = shutil.which("sh") or r"C:\Program Files\Git\bin\sh.exe"
CHAT = 42


class FakeTelegram:
    def __init__(self):
        self.calls, self.updates, self.next_mid, self.next_uid = [], [], 100, 1
        self.inline = False  # whether the bot has inline mode on (@BotFather /setinline)
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                with fake.lock:
                    result = True
                    if method == "sendMessage":
                        fake.next_mid += 1
                        body["_mid"] = fake.next_mid
                        result = {"message_id": fake.next_mid}
                    elif method == "getUpdates":
                        off = body.get("offset", 0)
                        result = [u for u in fake.updates if u["update_id"] >= off]
                    elif method == "getMe":
                        result = {"username": "test_bot", "supports_inline_queries": fake.inline}
                    fake.calls.append((method, body))
                data = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def push(self, update):
        with self.lock:
            update["update_id"] = self.next_uid
            self.next_uid += 1
            self.updates.append(update)

    def sent(self):
        with self.lock:
            return [b for m, b in self.calls if m == "sendMessage"]

    def edits(self):
        with self.lock:
            return [b for m, b in self.calls if m == "editMessageText"]

    def find(self, word):
        return [m for m in self.sent() if word in m["text"]]


def msg(text, reply_to=None, sender=CHAT, reply_text=None, quote=None):
    m = {"chat": {"id": CHAT}, "from": {"id": sender}, "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to, "text": reply_text or ""}
    if quote:
        m["quote"] = {"text": quote}  # Telegram's "quote": the part of the message you selected
    return {"message": m}


def tap(mid, choice, sender=CHAT):
    return {"callback_query": {"id": "cb", "data": str(choice), "from": {"id": sender}, "message": {"message_id": mid, "chat": {"id": CHAT}}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        self.home = Path(tempfile.mkdtemp())
        (self.home / "config.json").write_text(json.dumps({"token": "T", "chat_id": CHAT, "owner_id": CHAT}))
        (self.home / "open_terminal.py").write_text(
            "import json, sys, os\nopen(os.path.join(os.path.dirname(__file__), 'opened.log'), 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
        router = self.home / "router.py"
        router.write_text("import json, os, sys\nsys.stdout.reconfigure(encoding='utf-8')\n"
                          "good = os.environ.get('FAKE_LOGGED_IN_CONFIG')\n"  # a Claude login that only works in one profile
                          "if good and os.environ.get('CLAUDE_CONFIG_DIR') != good:\n"
                          "    print(json.dumps({'is_error': True, 'result': 'Not logged in'})); raise SystemExit(1)\n"
                          # like claude -p: raw UTF-8 on stdout, accents not escaped
                          "sys.stdout.buffer.write(json.dumps({'result': os.environ.get('FAKE_ROUTE', '{}')}, ensure_ascii=False).encode('utf-8'))\n")
        # Never inherit a real terminal: a test must not type into the Orca or tmux pane it runs in.
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("ORCA_", "TMUX", "CLAUDE_TELEGRAM_", "CLAUDE_PROJECT_DIR"))}
        self.env = {**clean, "CLAUDE_TELEGRAM_HOME": str(self.home), "CLAUDE_TELEGRAM_API": self.tg.url,
                    "CLAUDE_TELEGRAM_TICK": "0.1", "CLAUDE_TELEGRAM_LONG_POLL": "0", "CLAUDE_TELEGRAM_POLLER_IDLE": "5",
                    "CLAUDE_TELEGRAM_CLAUDE": str(router),
                    # never open a real terminal window from a test
                    "CLAUDE_TELEGRAM_OPEN_TERMINAL": str(self.home / "open_terminal.py"),
                    "CLAUDE_TELEGRAM_IDLE_SECONDS": "0"}  # tests must not depend on whether someone is using this machine
        self.procs = []
        # A stand-in for the claude process: hooks watch it, and killing it in tearDown ends every waiter they left.
        self.claude = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        self.env["CLAUDE_TELEGRAM_CLAUDE_PID"] = str(self.claude.pid)

    def tearDown(self):
        self.claude.kill()
        self.claude.wait()
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                p.wait(5)
            for f in (p.stdout, p.stderr):
                f.close()
        shutil.rmtree(self.home / "state" / "listening", ignore_errors=True)  # any listener still running leaves
        owner = self.home / "state" / "poller.lock" / "owner"
        if owner.exists():
            try:
                os.kill(int(owner.read_text()), signal.SIGTERM)
            except OSError:
                pass
        time.sleep(0.3)
        self.tg.server.shutdown()
        self.tg.server.server_close()

    def hook(self, event, data, background=False):
        p = subprocess.Popen([SH, str(RUN), "hook", event], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        self.procs.append(p)
        p.stdin.write(json.dumps(data).encode())
        p.stdin.close()
        return p if background else self.finish(p)

    def finish(self, p, timeout=15):
        p.wait(timeout)
        return p.returncode, p.stdout.read().decode(), p.stderr.read().decode()

    def wait_for(self, cond, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return
            time.sleep(0.05)
        self.fail("timed out")

    def state(self, *parts):
        return self.home / "state" / Path(*parts)

    def go_away(self):
        self.state().mkdir(parents=True, exist_ok=True)
        self.state("away").touch()

    def listening(self, sid):
        return lambda: self.state("listening", sid).exists()


BASE = {"session_id": "s1", "cwd": "/work/my-shop", "transcript_path": "/nope"}
OTHER = {"session_id": "s2", "cwd": "/work/blog", "transcript_path": "/nope"}


def stop(base, text="", crons=None, **kw):
    return {**base, "last_assistant_message": text, "session_crons": crons or [], "background_tasks": [], **kw}


class Questions(Base):
    def test_question_while_away_gets_reply_back_to_claude(self):
        self.hook("prompt", {**BASE, "prompt": "add stripe checkout"})
        self.go_away()
        p = self.hook("stop", stop(BASE, "Should I use test keys or live keys?"), background=True)
        self.wait_for(lambda: self.tg.find("live keys"))
        q = self.tg.find("live keys")[0]
        self.assertIn("my-shop", q["text"])
        self.assertIn("add stripe checkout", q["text"])
        self.tg.push(msg("test keys", reply_to=q["_mid"]))
        code, _, err = self.finish(p)
        self.assertEqual(code, 2)
        self.assertIn("answered from Telegram: test keys", err)
        self.wait_for(lambda: any("Sent: test keys" in e["text"] for e in self.tg.edits()))

    def test_typed_number_stays_a_number(self):
        self.go_away()
        p = self.hook("stop", stop(BASE, "Option 1 or 2?"), background=True)
        self.wait_for(lambda: self.tg.sent())
        self.tg.push(msg("2", reply_to=self.tg.sent()[0]["_mid"]))
        code, _, err = self.finish(p)
        self.assertTrue(err.rstrip().endswith(": 2"), err)

    def test_quick_reply_button_answers_text_question(self):
        self.go_away()
        p = self.hook("stop", stop(BASE, "Deploy now?"), background=True)
        self.wait_for(lambda: self.tg.sent())
        q = self.tg.sent()[0]
        self.assertEqual([r[0]["text"] for r in q["reply_markup"]["inline_keyboard"]], ["Yes", "No", "Do what you think is best"])
        self.tg.push(tap(q["_mid"], 2))
        code, _, err = self.finish(p)
        self.assertEqual(code, 2)
        self.assertIn("Do what you think is best", err)

    def test_reply_from_stranger_is_ignored_and_terminal_answer_closes_it(self):
        self.go_away()
        p = self.hook("stop", stop(BASE, "Ok?"), background=True)
        self.wait_for(lambda: self.tg.sent())
        mid = self.tg.sent()[0]["_mid"]
        self.tg.push(msg("rm -rf", reply_to=mid, sender=7))
        self.tg.push(tap(mid, 0, sender=7))
        time.sleep(1)
        self.assertIsNone(p.poll())
        self.hook("prompt", {**BASE, "prompt": "never mind"})  # you answered in the terminal
        code, _, _ = self.finish(p)
        self.assertEqual(code, 0)
        self.wait_for(lambda: any("Answered in the terminal" in e["text"] for e in self.tg.edits()))

    def test_no_message_when_present(self):
        self.hook("prompt", {**BASE, "prompt": "hi"})
        p = self.hook("stop", stop(BASE, "Which one?"), background=True)
        self.wait_for(self.listening("s1"))
        time.sleep(0.5)
        self.hook("prompt", {**BASE, "prompt": "the first"})
        code, _, _ = self.finish(p)
        self.assertEqual((code, self.tg.sent()), (0, []))

    def test_finished_turn_without_question_is_silent_but_listens(self):
        self.go_away()
        p = self.hook("stop", stop(BASE, "Done. Tests pass."), background=True)
        self.wait_for(self.listening("s1"))
        time.sleep(0.5)
        self.assertIsNone(p.poll())
        self.assertEqual(self.tg.sent(), [])


class Lifecycle(Base):
    def test_listener_dies_with_its_hook(self):
        if os.name == "nt":
            self.skipTest("Windows can't tell a killed Git Bash parent; the poller's check on the claude process covers it")
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        time.sleep(1)  # let listen.sh take over from the Python part
        p.terminate()  # Claude Code kills the hook when the session closes; on Windows the child must notice by itself
        p.wait(5)
        self.wait_for(lambda: not self.state("listening", "s1").exists())

    def test_listener_ends_when_claude_dies(self):
        claude = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # stands in for the claude process
        self.env["CLAUDE_TELEGRAM_CLAUDE_PID"] = str(claude.pid)
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        time.sleep(1)
        self.assertIsNone(p.poll())
        claude.kill()
        claude.wait()
        self.assertEqual(self.finish(p)[0], 0)
        self.tg.push(msg("/status"))
        self.wait_for(lambda: self.tg.find("Open sessions: 0"))

    def test_listener_restarts_a_poller_that_died(self):
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        owner = self.state("poller.lock", "owner")
        self.wait_for(owner.exists)
        old = int(owner.read_text())
        os.kill(old, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)  # crashes: the lock stays behind
        self.wait_for(lambda: owner.exists() and owner.read_text().strip() not in ("", str(old)), timeout=20)
        self.tg.push(msg("@shop still there?"))
        code, _, err = self.finish(p)
        self.assertEqual(code, 2)
        self.assertIn("still there?", err)

    def test_session_end_stops_listening(self):
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.hook("end", {**BASE, "reason": "exit"})
        self.assertEqual(self.finish(p)[0], 0)


class Messages(Base):
    def test_at_project_message_wakes_idle_session_and_reply_comes_back(self):
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@shop how is the checkout going"))
        code, _, err = self.finish(p)
        self.assertEqual(code, 2)
        self.assertIn("Message from the user via Telegram: how is the checkout going", err)
        self.assertTrue(self.tg.find("→ my-shop: sent"))
        p = self.hook("stop", stop(BASE, "Checkout works, two tests left."), background=True)
        self.wait_for(lambda: self.tg.find("two tests left"))
        self.assertIn("Reply · my-shop", self.tg.find("two tests left")[0]["text"])

    def test_at_project_beats_the_only_open_question(self):
        self.go_away()
        p1 = self.hook("stop", stop(BASE, "Should I use test keys or live keys?"), background=True)
        p2 = self.hook("stop", stop(OTHER, "Done."), background=True)
        self.wait_for(lambda: self.tg.find("live keys"))
        self.wait_for(self.listening("s2"))
        self.tg.push(msg("@blog is the new post ready?"))
        code, _, err = self.finish(p2)
        self.assertEqual(code, 2)
        self.assertIn("new post", err)
        self.assertIsNone(p1.poll())

    def test_at_unknown_project_says_so_instead_of_offering_others(self):
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@payroll is the export done?"))
        self.wait_for(lambda: self.tg.find("@payroll"))
        reply = self.tg.find("@payroll")[0]
        self.assertNotIn("reply_markup", reply)
        self.assertIn("my-shop", reply["text"])  # tells you which ones it does know
        self.assertIsNone(p.poll())

    def test_project_colon_session_talks_to_that_session(self):
        a = {**BASE, "session_id": "aaaa1111"}
        b = {**BASE, "session_id": "bbbb2222"}
        pa = self.hook("stop", stop(a, "Done."), background=True)
        pb = self.hook("stop", stop(b, "Done."), background=True)
        self.wait_for(self.listening("aaaa1111"))
        self.wait_for(self.listening("bbbb2222"))
        self.tg.push(msg("/sessions @shop"))
        self.wait_for(lambda: self.tg.find("@my-shop:bbbb"))
        self.assertIn("@my-shop:aaaa", self.tg.find("@my-shop:bbbb")[0]["text"])
        self.tg.push(msg("@shop:bbbb what are you doing?"))
        code, _, err = self.finish(pb)
        self.assertEqual(code, 2)
        self.assertIn("what are you doing?", err)
        self.assertIsNone(pa.poll())

    def test_project_name_is_where_the_session_started_not_where_it_cd_to(self):
        self.env["CLAUDE_PROJECT_DIR"] = "/work/my-shop"
        p = self.hook("stop", stop({**BASE, "cwd": "/work/my-shop/packages/api"}, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("/projects"))
        self.wait_for(lambda: self.tg.find("@my-shop"))
        self.assertNotIn("@api", self.tg.find("@my-shop")[0]["text"])

    def test_at_name_that_is_no_project_asks_the_router(self):
        self.env["FAKE_ROUTE"] = json.dumps({"session": "s2"})
        p1 = self.hook("stop", stop(BASE, "Done."), background=True)
        p2 = self.hook("stop", stop(OTHER, "the api package is blocked after attempt 3."), background=True)
        self.wait_for(self.listening("s1"))
        self.wait_for(self.listening("s2"))
        self.tg.push(msg("@api go for attempt 4"))
        code, _, err = self.finish(p2)
        self.assertEqual(code, 2)
        self.assertIn("go for attempt 4", err)
        self.assertIsNone(p1.poll())

    def test_reply_to_a_sent_confirmation_goes_to_that_session(self):
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@shop first"))
        self.assertEqual(self.finish(p)[0], 2)
        conf = self.tg.find("→ my-shop: sent")[0]
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("and a second thing", reply_to=conf["_mid"], reply_text=conf["text"]))
        code, _, err = self.finish(p)
        self.assertEqual(code, 2)
        self.assertIn("and a second thing", err)

    def test_quoting_a_session_line_of_projects_talks_to_it(self):
        a = {**BASE, "session_id": "aaaa1111"}
        b = {**BASE, "session_id": "bbbb2222"}
        pa = self.hook("stop", stop(a, "Done."), background=True)
        pb = self.hook("stop", stop(b, "Done."), background=True)
        self.wait_for(self.listening("aaaa1111"))
        self.wait_for(self.listening("bbbb2222"))
        self.tg.push(msg("/projects"))
        self.wait_for(lambda: self.tg.find("@my-shop:bbbb"))
        listing = self.tg.find("@my-shop:bbbb")[0]
        line = next(l for l in listing["text"].splitlines() if "@my-shop:bbbb" in l)
        self.tg.push(msg("what are you on?", reply_to=listing["_mid"], reply_text=listing["text"], quote=line))
        code, _, err = self.finish(pb)
        self.assertEqual(code, 2)
        self.assertIn("what are you on?", err)
        self.assertIsNone(pa.poll())

    def test_general_answers_and_relays_what_you_ask_it_to(self):
        self.env["FAKE_ROUTE"] = json.dumps({"reply": "Both are idle; I asked the blog for its status.",
                                             "send": [{"session": "s2", "message": "send me your status"}]})
        p1 = self.hook("stop", stop(BASE, "Done."), background=True)
        p2 = self.hook("stop", stop(OTHER, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.wait_for(self.listening("s2"))
        self.tg.push(msg("@general how is everything? ask the blog for a status"))
        self.wait_for(lambda: self.tg.find("Both are idle"))
        proposal = self.tg.find("Both are idle")[0]
        time.sleep(1)
        self.assertIsNone(p2.poll())  # nothing is sent before you confirm: session text could have steered the orchestrator
        self.tg.push(tap(proposal["_mid"], 0))
        code, _, err = self.finish(p2)
        self.assertEqual(code, 2)
        self.assertIn("send me your status", err)
        self.assertIn("general orchestrator", err)  # the session knows who relayed it
        answer = proposal
        self.assertIn("blog", answer["text"])  # says where it sent what
        self.assertIsNone(p1.poll())
        # replying to its answer keeps talking to the orchestrator, not to a session
        # (the fake orchestrator gives the same answer again: the running poller keeps its environment)
        self.tg.push(msg("anything else?", reply_to=answer["_mid"], reply_text=answer["text"]))
        self.wait_for(lambda: len(self.tg.find("General ·")) == 2)
        self.assertFalse(self.tg.find("don't know which session"))
        self.assertIsNone(p1.poll())

    def test_accents_survive_a_non_utf8_system_encoding(self):
        # Windows decodes subprocess output with its old code page unless told otherwise
        self.env.update(PYTHONUTF8="0", LC_ALL="C", LANG="C", PYTHONIOENCODING="")
        self.env["FAKE_ROUTE"] = json.dumps({"reply": "Está todo listo: sesión ñandú", "send": []}, ensure_ascii=False)
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@general ¿cómo va?"))
        self.wait_for(lambda: self.tg.find("General ·"))
        self.assertIn("Está todo listo: sesión ñandú", self.tg.find("General ·")[0]["text"])

    def test_general_never_relays_commands(self):
        self.env["FAKE_ROUTE"] = json.dumps({"reply": "Done.", "send": [{"session": "s1", "message": "/clear"}]})
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@general clear my-shop"))
        self.wait_for(lambda: self.tg.find("not sent"))
        time.sleep(1)
        self.assertIsNone(p.poll())

    def test_router_picks_the_session(self):
        self.env["FAKE_ROUTE"] = json.dumps({"session": "s2"})
        p1 = self.hook("stop", stop(BASE, "Done."), background=True)
        p2 = self.hook("stop", stop(OTHER, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.wait_for(self.listening("s2"))
        self.tg.push(msg("how is the comments section going?"))
        code, _, err = self.finish(p2)
        self.assertEqual(code, 2)
        self.assertIn("comments section", err)
        self.assertIsNone(p1.poll())

    def test_router_uses_a_profile_that_is_logged_in(self):
        self.env["FAKE_ROUTE"] = json.dumps({"session": "s2"})
        self.env["FAKE_LOGGED_IN_CONFIG"] = "/profiles/good"
        self.env["CLAUDE_CONFIG_DIR"] = "/profiles/expired"  # the poller starts with this one
        p1 = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.env["CLAUDE_CONFIG_DIR"] = "/profiles/good"
        p2 = self.hook("stop", stop(OTHER, "Done."), background=True)
        self.wait_for(self.listening("s2"))
        self.tg.push(msg("how is the comments section going?"))
        code, _, err = self.finish(p2)
        self.assertEqual(code, 2)
        self.assertIsNone(p1.poll())

    def test_router_unsure_asks_with_buttons(self):
        self.env["FAKE_ROUTE"] = json.dumps({"session": None, "candidates": ["s1", "s2"]})
        p1 = self.hook("stop", stop(BASE, "Done."), background=True)
        p2 = self.hook("stop", stop(OTHER, "Done."), background=True)
        self.wait_for(self.listening("s2"))
        self.tg.push(msg("status?"))
        self.wait_for(lambda: self.tg.find("Which session"))
        q = self.tg.find("Which session")[0]
        labels = [r[0]["text"] for r in q["reply_markup"]["inline_keyboard"]]
        self.tg.push(tap(q["_mid"], next(i for i, l in enumerate(labels) if l.startswith("blog"))))
        code, _, err = self.finish(p2)
        self.assertIn("status?", err)
        self.assertIsNone(p1.poll())

    def test_message_for_busy_session_waits_for_its_turn_to_end(self):
        p = self.hook("stop", stop(OTHER, "Done."), background=True)  # keeps the poller running
        self.wait_for(self.listening("s2"))
        self.hook("prompt", {**BASE, "prompt": "long task"})  # s1 is busy: no listener
        self.tg.push(msg("@shop also update the README"))
        self.wait_for(lambda: self.tg.find("busy"))
        code, _, err = self.hook("stop", stop(BASE, "Long task done."))
        self.assertEqual(code, 2)
        self.assertIn("update the README", err)

    def test_reply_to_old_message_goes_to_its_session(self):
        cron = {"id": "c1", "schedule": "*/5 * * * *", "recurring": True, "prompt": "check the deploy"}
        self.hook("stop", stop(OTHER, "", [cron]), background=True)
        self.wait_for(self.listening("s2"))
        self.wait_for(lambda: self.tg.find("Loop started"))
        loop = self.tg.find("Loop started")[0]
        p = self.hook("stop", stop(BASE, "Done."), background=True)  # another listener, to prove routing
        self.wait_for(self.listening("s1"))
        self.hook("prompt", {**OTHER, "prompt": "check the deploy"})  # loop iteration: s2 busy
        self.tg.push(msg("stop the loop", reply_to=loop["_mid"]))
        self.wait_for(lambda: self.tg.find("blog: it's busy"))
        code, _, err = self.hook("stop", stop(OTHER, "Deploy fine."))
        self.assertIn("stop the loop", err)
        self.assertIsNone(p.poll())


class Commands(Base):
    def fake_orca(self):
        orca = self.home / "orca.py"
        orca.write_text("import json, sys, os\nopen(os.environ['FAKE_ORCA_LOG'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\nprint('{\"ok\": true}')\n")
        self.env.update(CLAUDE_TELEGRAM_ORCA=str(orca), FAKE_ORCA_LOG=str(self.home / "orca.log"), ORCA_TERMINAL_HANDLE="term_abc")
        return self.home / "orca.log"

    def test_new_session_opens_one_in_the_project_and_its_answer_comes_back(self):
        log = self.fake_orca()
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("/new-session @shop write the README"))
        self.wait_for(lambda: log.exists() and '"launch"' in log.read_text())
        launch = [json.loads(l) for l in log.read_text().splitlines() if '"launch"' in l][0]
        self.assertEqual(launch, ["agent", "launch", "--agent", "claude", "--worktree", "path:/work/my-shop",
                                  "--surface", "terminal", "--prompt", "write the README", "--json"])
        self.wait_for(lambda: self.tg.find("new session"))
        # the new session starts with that prompt: it isn't you typing, and its answer goes to Telegram
        self.hook("prompt", {"session_id": "s9", "cwd": "/work/my-shop", "prompt": "write the README"})
        self.hook("stop", stop({**BASE, "session_id": "s9"}, "README written."), background=True)
        self.wait_for(lambda: self.tg.find("README written."))
        self.assertIn("my-shop", self.tg.find("README written.")[0]["text"])

    def test_new_session_without_orca_opens_a_terminal_window(self):
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("/new-session @shop write the README"))
        opened = self.home / "opened.log"
        self.wait_for(opened.exists)
        self.assertEqual(json.loads(opened.read_text().splitlines()[0]), ["/work/my-shop", "write the README"])
        self.wait_for(lambda: self.tg.find("new session"))

    def test_new_session_general_goes_to_orcas_floating_terminal(self):
        log = self.fake_orca()
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("/new-session @general what's on my plate today?"))
        self.wait_for(lambda: log.exists() and '"launch"' in log.read_text())
        launch = [json.loads(l) for l in log.read_text().splitlines() if '"launch"' in l][0]
        self.assertIn("id:global-floating-terminal", launch)

    def test_slash_command_is_typed_into_an_orca_terminal(self):
        log = self.fake_orca()
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@shop /compact"))
        self.wait_for(log.exists)
        self.assertEqual(json.loads(log.read_text().splitlines()[0]), ["terminal", "send", "--terminal", "term_abc", "--text", "/compact", "--enter", "--json"])
        self.wait_for(lambda: self.tg.find("typed /compact"))
        self.assertFalse(self.state("deliver", "s1.txt").exists())

    def test_only_slash_commands_are_typed(self):
        log = self.fake_orca()
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@shop !rm -rf ~"))  # shell escape: goes to Claude as a message, never typed
        code, _, err = self.finish(p)
        self.assertEqual(code, 2)
        self.assertIn("!rm -rf ~", err)
        self.assertFalse(log.exists())

    def test_router_never_types_commands(self):
        log = self.fake_orca()
        self.env["FAKE_ROUTE"] = json.dumps({"session": "s1"})
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("/clear"))  # no @project: a guessed session must not get its context wiped
        self.wait_for(lambda: self.tg.find("name the session"))
        self.assertFalse(log.exists())
        self.assertIsNone(p.poll())

    def test_command_without_terminal_access_explains(self):
        p = self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@shop /compact"))
        self.wait_for(lambda: self.tg.find("Orca or tmux"))
        self.assertIsNone(p.poll())


class Loops(Base):
    def test_loop_start_notified_once(self):
        cron = {"id": "c1", "schedule": "*/5 * * * *", "recurring": True, "prompt": "check the deploy"}
        for _ in range(2):
            p = self.hook("stop", stop(BASE, "Checked?", [cron]), background=True)
            self.wait_for(self.listening("s1"))
            self.hook("prompt", {**BASE, "prompt": "check the deploy"})
            self.finish(p)
        loops = self.tg.find("Loop started")
        self.assertEqual(len(loops), 1)
        self.assertIn("check the deploy", loops[0]["text"])
        self.assertFalse(self.tg.find("Question"))  # a session with a loop isn't waiting on you

    def test_loop_iteration_does_not_count_as_presence(self):
        cron = {"id": "c1", "schedule": "*/5 * * * *", "recurring": True, "prompt": "check the deploy"}
        self.hook("stop", stop(BASE, "", [cron]), background=True)
        self.wait_for(self.listening("s1"))
        self.go_away()
        self.hook("prompt", {**BASE, "prompt": "check the deploy"})
        self.assertTrue(self.state("away").exists())

    def test_wake_up_from_telegram_does_not_count_as_presence(self):
        self.hook("prompt", {**BASE, "prompt": "add stripe"})
        self.go_away()
        self.hook("prompt", {**BASE, "prompt": "<task-notification>\n<summary>Stop hook feedback</summary>\n</task-notification>"})
        self.assertTrue(self.state("away").exists())
        self.assertEqual(json.loads(self.state("sessions", "s1.json").read_text())["prompt"], "add stripe")


class Dialogs(Base):
    ASK = {**BASE, "tool_input": {"questions": [{"question": "Color?", "header": "C", "multiSelect": False,
                                                  "options": [{"label": "Red", "description": "r"}, {"label": "Blue", "description": "b"}]}]}}

    def test_ask_present_shows_normal_dialog(self):
        self.assertEqual(self.hook("ask", self.ASK)[:2], (0, ""))

    def test_ask_away_answered_with_button(self):
        self.go_away()
        p = self.hook("ask", self.ASK, background=True)
        self.wait_for(lambda: self.tg.sent())
        q = self.tg.sent()[0]
        self.assertEqual(len(q["reply_markup"]["inline_keyboard"]), 2)
        self.tg.push(tap(q["_mid"], 1))
        code, out, _ = self.finish(p)
        res = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(res["permissionDecision"], "allow")
        self.assertEqual(res["updatedInput"]["answers"], {"Color?": "Blue"})

    def test_ask_away_released_when_you_come_back(self):
        self.go_away()
        p = self.hook("ask", self.ASK, background=True)
        self.wait_for(lambda: self.tg.sent())
        self.hook("prompt", {"session_id": "other", "cwd": "/x", "prompt": "typing elsewhere"})
        self.assertEqual(self.finish(p)[:2], (0, ""))
        self.wait_for(lambda: any("Moved to the terminal" in e["text"] for e in self.tg.edits()))

    def test_permission_denied_with_reason(self):
        self.go_away()
        p = self.hook("permission", {**BASE, "tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}, background=True)
        self.wait_for(lambda: self.tg.sent())
        self.tg.push(msg("no, keep build", reply_to=self.tg.sent()[0]["_mid"]))
        code, out, _ = self.finish(p)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"], {"behavior": "deny", "message": "no, keep build"})

    def test_loop_permission_goes_to_telegram_even_when_you_are_at_the_computer(self):
        p = self.hook("stop", stop(BASE, "Cycle done.", crons=[{"id": "c1", "cron": "*/30 * * * *", "prompt": "run a cycle"}]), background=True)
        self.wait_for(self.listening("s1"))
        # you're at the computer, but nobody watches a loop: it would sit on this prompt for hours
        q = self.hook("permission", {**BASE, "tool_name": "Bash", "tool_input": {"command": "rm old.log"}}, background=True)
        self.wait_for(lambda: self.tg.find("rm old.log"))
        self.hook("prompt", {"session_id": "other", "cwd": "/x", "prompt": "typing elsewhere"})
        time.sleep(1)
        self.assertIsNone(q.poll())  # being at the computer doesn't move it back to a terminal nobody is looking at
        self.tg.push(tap(self.tg.find("rm old.log")[0]["_mid"], 0))
        code, out, _ = self.finish(q)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"], {"behavior": "allow"})

    def test_permission_goes_to_telegram_in_any_session_even_when_present(self):
        q = self.hook("permission", {**BASE, "tool_name": "Bash", "tool_input": {"command": "rm notes.md"}}, background=True)
        self.wait_for(lambda: self.tg.find("rm notes.md"))
        self.hook("prompt", {"session_id": "other", "cwd": "/x", "prompt": "typing elsewhere"})
        time.sleep(1)
        self.assertIsNone(q.poll())
        self.tg.push(tap(self.tg.find("rm notes.md")[0]["_mid"], 1))
        code, out, _ = self.finish(q)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"]["behavior"], "deny")

    def test_permission_answered_in_the_terminal_is_dismissed_in_telegram(self):
        # Claude Code shows the dialog in the terminal while the hook waits; answering there runs the tool
        transcript = self.home / "t.jsonl"
        call = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "rm notes.md"}}]}}
        transcript.write_text(json.dumps(call) + "\n")
        q = self.hook("permission", {**BASE, "transcript_path": str(transcript), "tool_name": "Bash",
                                     "tool_input": {"command": "rm notes.md"}}, background=True)
        self.wait_for(lambda: self.tg.find("rm notes.md"))
        with open(transcript, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}}) + "\n")
        self.assertEqual(self.finish(q)[:2], (0, ""))  # no decision: the terminal already made it
        self.wait_for(lambda: any("Answered in the terminal" in e["text"] for e in self.tg.edits()))

    def auto_allow(self):
        cfg = json.loads((self.home / "config.json").read_text())
        (self.home / "config.json").write_text(json.dumps({**cfg, "auto_allow_in_bypass": True}))

    def test_bypass_session_prompts_are_allowed_without_asking_when_enabled(self):
        self.auto_allow()
        code, out, _ = self.hook("permission", {**BASE, "permission_mode": "bypassPermissions", "tool_name": "Bash",
                                                "tool_input": {"command": "rm -rf dist"}})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"], {"behavior": "allow"})
        self.assertFalse(self.tg.sent())

    def test_subagents_of_a_bypass_session_are_allowed_too(self):
        self.auto_allow()
        self.hook("prompt", {**BASE, "permission_mode": "bypassPermissions", "prompt": "build it"})
        code, out, _ = self.hook("permission", {**BASE, "agent_id": "bg1", "permission_mode": "default", "tool_name": "Bash",
                                                "tool_input": {"command": "rm tmp.txt"}})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"], {"behavior": "allow"})

    def test_protected_paths_are_never_allowed_without_asking(self):
        self.auto_allow()  # editing hooks or permissions would let a misled agent give itself more power
        q = self.hook("permission", {**BASE, "permission_mode": "bypassPermissions", "tool_name": "Edit",
                                     "tool_input": {"file_path": "/work/my-shop/.claude/settings.json"}}, background=True)
        self.wait_for(lambda: self.tg.find("settings.json"))
        self.tg.push(tap(self.tg.find("settings.json")[0]["_mid"], 1))
        self.finish(q)

    def test_protected_path_tricks_still_ask(self):
        self.auto_allow()
        for cmd in ["cat x > ~/.CLAUDE/settings.json", "echo x >> .cl''aude/hooks.json", "rm -rf .cl*/", "cd repo && rm -rf .g?t",
                    "cp a $HOME/.claude-telegram/config.json"]:
            q = self.hook("permission", {**BASE, "permission_mode": "bypassPermissions", "tool_name": "Bash",
                                         "tool_input": {"command": cmd}}, background=True)
            self.wait_for(lambda: self.tg.find(cmd.split()[-1][:6]))
            self.tg.push(tap(self.tg.find(cmd.split()[-1][:6])[-1]["_mid"], 1))
            self.assertEqual(json.loads(self.finish(q)[1])["hookSpecificOutput"]["decision"]["behavior"], "deny", cmd)

    def test_leaving_bypass_stops_auto_allowing_its_subagents(self):
        self.auto_allow()
        self.hook("prompt", {**BASE, "permission_mode": "bypassPermissions", "prompt": "build it"})
        self.hook("prompt", {**BASE, "permission_mode": "default", "prompt": "now carefully"})
        q = self.hook("permission", {**BASE, "agent_id": "bg1", "permission_mode": "default", "tool_name": "Bash",
                                     "tool_input": {"command": "rm tmp.txt"}}, background=True)
        self.wait_for(lambda: self.tg.find("rm tmp.txt"))
        self.tg.push(tap(self.tg.find("rm tmp.txt")[0]["_mid"], 1))
        self.finish(q)

    def test_bypass_prompts_still_ask_when_not_enabled(self):
        q = self.hook("permission", {**BASE, "permission_mode": "bypassPermissions", "tool_name": "Bash",
                                     "tool_input": {"command": "rm -rf dist"}}, background=True)
        self.wait_for(lambda: self.tg.find("rm -rf dist"))
        self.tg.push(tap(self.tg.find("rm -rf dist")[0]["_mid"], 1))
        self.finish(q)

    def test_permissions_can_stay_in_the_terminal_when_present(self):
        cfg = json.loads((self.home / "config.json").read_text())
        (self.home / "config.json").write_text(json.dumps({**cfg, "permissions_always": False}))
        self.assertEqual(self.hook("permission", {**BASE, "tool_name": "Bash", "tool_input": {"command": "ls"}})[:2], (0, ""))

    def test_subagent_permission_reaches_telegram(self):
        self.go_away()  # a subagent's prompt blocks its parent session just the same
        q = self.hook("permission", {**BASE, "agent_id": "sub1", "tool_name": "Bash", "tool_input": {"command": "rm tmp.txt"}}, background=True)
        self.wait_for(lambda: self.tg.find("rm tmp.txt"))
        self.tg.push(tap(self.tg.find("rm tmp.txt")[0]["_mid"], 0))
        code, out, _ = self.finish(q)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"], {"behavior": "allow"})

    def test_away_command_from_telegram(self):
        self.hook("stop", stop(OTHER, "Done."), background=True)  # a listener keeps the poller running
        self.wait_for(self.listening("s2"))
        self.tg.push(msg("/away"))
        self.wait_for(lambda: self.state("away").exists())
        self.hook("ask", self.ASK, background=True)
        self.wait_for(lambda: self.tg.find("Color?"))

    def test_idle_pc_counts_as_away(self):
        self.hook("prompt", {**BASE, "prompt": "hi"})  # typed a moment ago, then left the computer
        self.env["CLAUDE_TELEGRAM_IDLE_SECONDS"] = "200"
        self.hook("ask", self.ASK, background=True)
        self.wait_for(lambda: self.tg.find("Color?"))

    def test_using_the_pc_counts_as_present_even_without_prompts(self):
        self.state().mkdir(parents=True)
        self.state("activity").touch()
        old = time.time() - 3600
        os.utime(self.state("activity"), (old, old))  # no prompt for an hour...
        self.env["CLAUDE_TELEGRAM_IDLE_SECONDS"] = "5"  # ...but the mouse moved 5 s ago
        self.assertEqual(self.hook("ask", self.ASK)[:2], (0, ""))
        self.assertEqual(self.tg.sent(), [])


class Failures(Base):
    def test_failure_notified_when_away(self):
        self.go_away()
        self.assertEqual(self.hook("failure", {**BASE, "error": "rate_limit", "last_assistant_message": "API Error: Rate limit reached"})[0], 0)
        self.wait_for(lambda: self.tg.find("Rate limit reached"))

    def test_failure_silent_when_you_come_back(self):
        self.hook("stop", stop(OTHER, "Done."), background=True)  # keeps the poller running
        self.hook("failure", {**BASE, "error": "server_error", "last_assistant_message": "API Error: 500"})
        self.hook("prompt", {**BASE, "prompt": "retry"})
        self.go_away()
        time.sleep(1)
        self.assertFalse(self.tg.find("API Error"))


class Admin(Base):
    def test_mute_project(self):
        self.hook("stop", stop(OTHER, "Done."), background=True)  # keeps the poller running
        self.wait_for(self.listening("s2"))
        self.go_away()
        self.tg.push(msg("/mute my-shop"))
        self.wait_for(lambda: self.tg.find("Muted: my-shop"))
        cron = {"id": "c1", "schedule": "* * * * *", "recurring": True, "prompt": "x"}
        self.hook("stop", stop(BASE, "", [cron]), background=True)
        self.hook("stop", stop(BASE, "Ok?"), background=True)
        self.assertEqual(self.hook("ask", Dialogs.ASK)[:2], (0, ""))
        time.sleep(0.5)
        self.assertFalse(self.tg.find("· my-shop"))
        self.tg.push(msg("/unmute my-shop"))
        self.wait_for(lambda: self.tg.find("Muted: none"))
        self.hook("stop", stop(BASE, "Now?"), background=True)
        self.wait_for(lambda: self.tg.find("Question · my-shop"))

    def test_other_member_of_owner_chat_is_ignored(self):
        self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("@shop delete everything", sender=7))
        time.sleep(1)
        self.assertFalse(self.state("deliver", "s1.txt").exists())

    def test_status_lists_sessions(self):
        self.hook("stop", stop(BASE, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.tg.push(msg("/status"))
        self.wait_for(lambda: self.tg.find("Open sessions: 1"))

    def test_projects_lists_each_project_with_its_name_to_type(self):
        self.hook("prompt", {**BASE, "prompt": "add stripe checkout"})
        self.hook("stop", stop(BASE, "Checkout done."), background=True)
        self.hook("stop", stop(OTHER, "Done."), background=True)
        self.wait_for(self.listening("s1"))
        self.wait_for(self.listening("s2"))
        self.tg.push(msg("/projects"))
        self.wait_for(lambda: self.tg.find("@my-shop"))
        text = self.tg.find("@my-shop")[0]["text"]
        self.assertIn("@blog", text)
        self.assertIn("add stripe checkout", text)
        self.assertIn("Checkout done.", text)

    def two_shop_sessions(self):
        for sid in ("aaaa1111", "bbbb2222"):
            self.hook("stop", stop({**BASE, "session_id": sid}, "Done."), background=True)
        for sid in ("aaaa1111", "bbbb2222"):
            self.wait_for(self.listening(sid))
        self.tg.push(msg("/projects"))
        self.wait_for(lambda: self.tg.find("@my-shop:bbbb"))
        return self.tg.find("@my-shop:bbbb")[0]

    def test_projects_names_are_code_so_a_tap_copies_them(self):
        listing = self.two_shop_sessions()
        text = listing["text"]
        codes = {text[e["offset"]:e["offset"] + e["length"]] for e in listing.get("entities", []) if e["type"] == "code"}
        self.assertIn("@my-shop:aaaa", codes)
        self.assertIn("@my-shop:bbbb", codes)
        self.assertNotIn("reply_markup", listing)  # inline mode off: no buttons that would do nothing

    def test_with_inline_mode_buttons_put_the_name_in_the_chat_box(self):
        self.tg.inline = True
        listing = self.two_shop_sessions()
        fills = [b.get("switch_inline_query_current_chat") for row in listing["reply_markup"]["inline_keyboard"] for b in row]
        self.assertIn("@my-shop:bbbb ", fills)
        # Telegram then sends “@test_bot @my-shop:bbbb …”: the bot's own name is dropped
        self.tg.push(msg("@test_bot @my-shop:bbbb what are you on?"))
        self.wait_for(lambda: self.tg.find("→ my-shop: sent"))
        self.assertFalse(self.tg.find("Which session"))  # went straight to bbbb, no picking between the two

    def test_config_and_state_are_private(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions")
        subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(HERE)!r}); import claude_telegram as c; c.save(c.HOME / 'new.json', {{'token': 'x'}}, private=True)"], env=self.env, check=True)
        self.assertEqual((self.home / "new.json").stat().st_mode & 0o777, 0o600)
        fresh = self.home / "fresh"
        subprocess.run([sys.executable, str(SCRIPT), "hook", "prompt"], input=b'{"session_id": "s"}', env={**self.env, "CLAUDE_TELEGRAM_HOME": str(fresh)}, check=True)
        self.assertEqual({p.stat().st_mode & 0o777 for p in [fresh, *fresh.rglob("*")] if p.is_dir()}, {0o700})

    def test_system_idle_reads_a_number_on_this_os(self):
        sys.path.insert(0, str(HERE))
        import claude_telegram as ct
        idle = ct.system_idle()
        if sys.platform.startswith("linux") and idle is None:
            self.skipTest("no X11 idle source")
        self.assertIsInstance(idle, float)

    def test_setup_then_pair_trusts_only_private_chat_that_sends_the_code(self):
        (self.home / "config.json").unlink()
        self.tg.push({"message": {"chat": {"id": 5, "type": "private"}, "from": {"id": 5}, "text": "queued before setup"}})
        out = subprocess.run([sys.executable, str(SCRIPT), "setup", "-"], input="T\n", env=self.env, capture_output=True, text=True, check=True).stdout
        code = out.split(":")[1].split()[0]
        self.assertEqual(len(code), 8, out)
        self.tg.push({"message": {"chat": {"id": -9, "type": "group"}, "from": {"id": 6}, "text": code}})
        self.tg.push({"message": {"chat": {"id": 8, "type": "private"}, "from": {"id": 8}, "text": "wrong"}})
        self.tg.push({"message": {"chat": {"id": 7, "type": "private"}, "from": {"id": 7}, "text": code.upper()}})
        subprocess.run([sys.executable, str(SCRIPT), "pair", "--settings", str(self.home / "s.json")], env=self.env, capture_output=True, check=True, timeout=20)
        cfg = json.loads((self.home / "config.json").read_text())
        self.assertEqual((cfg["chat_id"], cfg["owner_id"]), (7, 7))
        self.assertNotIn("pairing", cfg)
        self.assertIn("UserPromptSubmit", json.loads((self.home / "s.json").read_text())["hooks"])

    def test_setup_refuses_token_as_argument(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "setup", "123:SECRET"], env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.tg.calls, [])

    def test_expired_pairing_code_is_refused(self):
        cfg = json.loads((self.home / "config.json").read_text())
        cfg["pairing"] = {"code": "abcd2345", "offset": 0, "bot": "b", "expires": time.time() - 1}
        (self.home / "config.json").write_text(json.dumps(cfg))
        self.tg.push({"message": {"chat": {"id": 66, "type": "private"}, "from": {"id": 66}, "text": "abcd2345"}})
        r = subprocess.run([sys.executable, str(SCRIPT), "pair", "--no-install"], env=self.env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(json.loads((self.home / "config.json").read_text())["chat_id"], CHAT)

    def test_install_is_idempotent_and_keeps_other_hooks(self):
        settings = self.home / "settings.json"
        other = {"type": "command", "command": "/usr/local/bin/other-hook"}
        old = {"type": "command", "command": '"/usr/bin/python3" "/x/claude_telegram.py" hook stop'}  # an older install
        settings.write_text(json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [other]}, {"hooks": [old]}]}}))
        for _ in range(2):
            subprocess.run([sys.executable, str(SCRIPT), "install", "--settings", str(settings)], env=self.env, check=True, capture_output=True)
        s = json.loads(settings.read_text())
        self.assertEqual(s["model"], "x")
        cmds = [h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(cmds), 2)
        self.assertIn("run.sh", cmds[1])
        subprocess.run([sys.executable, str(SCRIPT), "uninstall", "--settings", str(settings)], env=self.env, check=True, capture_output=True)
        self.assertEqual(json.loads(settings.read_text())["hooks"], {"Stop": [{"hooks": [other]}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
