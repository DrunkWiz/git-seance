"""Offline tests for git seance. The only network traffic is to a fake server on 127.0.0.1."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import git_seance as gs  # noqa: E402

M = gs.COMMIT_MARK


def commit(sha, author, date, subject, diff):
    return f"{M}{sha}\x1f{author}\x1f{date}\x1f{subject}\n{diff}"


SYNTHETIC_LOG = commit("a" * 40, "Ada", "2024-01-02", "remove the helpers", """
diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -10,9 +9,0 @@ class Thing:
-    def first(self):
-        def inner():
-            return 1
-        return inner()
-
-    def second(self, x):
-        return x * 2
-def outer():
-    pass
@@ -40,2 +30,0 @@
-    x = 1
-    y = 2
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,2 +0,0 @@
-def not_code():
-    pass
""") + commit("b" * 40, "Bob", "2023-05-06", "drop main", """
diff --git a/app.js b/app.js
--- a/app.js
+++ b/app.js
@@ -1,3 +0,0 @@
-export async function loadAll(a) {
-  return a;
-}
-function main() {
-}
""")


class ParseLogTests(unittest.TestCase):
    def setUp(self):
        self.ghosts = {g.name: g for g in gs.parse_log(SYNTHETIC_LOG)}

    def test_finds_each_deleted_function_once(self):
        self.assertEqual(set(self.ghosts), {"first", "second", "outer", "loadAll"})

    def test_body_ends_at_next_def_at_same_or_lower_indent(self):
        self.assertEqual(self.ghosts["first"].body, [
            "    def first(self):", "        def inner():", "            return 1",
            "        return inner()", ""])
        self.assertEqual(self.ghosts["second"].lines, 2)
        self.assertEqual(self.ghosts["outer"].body, ["def outer():", "    pass"])

    def test_commit_metadata(self):
        g = self.ghosts["second"]
        self.assertEqual((g.path, g.killed_sha, g.killed_by, g.killed_on, g.epitaph),
                         ("pkg/mod.py", "aaaaaaaa", "Ada", "2024-01-02", "remove the helpers"))
        self.assertEqual(self.ghosts["loadAll"].path, "app.js")

    def test_skips_non_code_files_and_boring_names(self):
        self.assertNotIn("not_code", self.ghosts)
        self.assertNotIn("main", self.ghosts)


def run_git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=Test", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


class GitRepoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        run_git(self.repo, "init", "-q")
        self.write("lib.py", "def keeper():\n    return 1\n\n\ndef doomed(x):\n    return x\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-q", "-m", "birth")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def test_still_alive(self):
        self.assertTrue(gs.still_alive(self.repo, "keeper"))
        self.assertTrue(gs.still_alive(self.repo, "doomed"))
        self.assertFalse(gs.still_alive(self.repo, "never_existed"))
        self.assertFalse(gs.still_alive(self.repo, "keep"))   # whole words only

    def test_deleted_function_becomes_a_ghost(self):
        self.write("lib.py", "def keeper():\n    return 1\n")
        run_git(self.repo, "commit", "-qam", "kill doomed")
        self.assertFalse(gs.still_alive(self.repo, "doomed"))
        ghosts = gs.find_ghosts(self.repo, 50)
        self.assertEqual([g.name for g in ghosts], ["doomed"])
        self.assertEqual(ghosts[0].epitaph, "kill doomed")
        gs.birth(self.repo, ghosts[0])
        self.assertEqual(ghosts[0].born_by, "Test")
        self.assertNotEqual(ghosts[0].born_on, "unknown")

    def test_moved_function_is_not_a_ghost(self):
        self.write("lib.py", "def keeper():\n    return 1\n")
        self.write("other.py", "def doomed(x):\n    return x\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-qm", "move doomed")
        self.assertEqual(gs.find_ghosts(self.repo, 50), [])


class FakeModel(BaseHTTPRequestHandler):
    """Speaks just enough Ollama /api/chat and OpenAI /chat/completions streaming."""
    requests = []
    fail_with = None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeModel.requests.append((self.path, dict(self.headers), body))
        if FakeModel.fail_with:
            self.send_response(FakeModel.fail_with)
            self.end_headers()
            self.wfile.write(b'{"error":"model \'gemma3:4b\' not found"}')
            return
        self.send_response(200)
        self.end_headers()
        words = ["I ", "was ", "deleted."]
        if self.path == "/api/chat":
            for w in words:
                self.wfile.write(json.dumps({"message": {"content": w}, "done": False}).encode() + b"\n")
            self.wfile.write(json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n")
        elif self.path == "/v1/chat/completions":
            self.wfile.write(b": keep-alive\n\n")
            self.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
            for w in words:
                self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": w}}]}).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeModel.requests.clear()
        FakeModel.fail_with = None
        patches = {"OLLAMA_URL": self.url, "API_BASE": self.url + "/v1",
                   "API_KEY": "test-key", "MODEL": "gemma3:4b", "NUM_GPU": ""}
        for name, value in patches.items():
            p = mock.patch.object(gs, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.ghost = gs.Ghost("doomed", "lib.py", ["def doomed(x):", "    return x"],
                              "abcd1234", "Ada", "2024-01-02", "kill doomed")

    def test_ollama_stream(self):
        self.assertEqual("".join(gs.ollama_stream("hello")), "I was deleted.")
        path, headers, body = FakeModel.requests[0]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["model"], "gemma3:4b")
        self.assertTrue(body["stream"])
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("options", body)

    def test_ollama_num_gpu_option(self):
        with mock.patch.object(gs, "NUM_GPU", "0"):
            "".join(gs.ollama_stream("hello"))
        self.assertEqual(FakeModel.requests[0][2]["options"], {"num_gpu": 0})

    def test_openai_stream(self):
        self.assertEqual("".join(gs.openai_stream("hello")), "I was deleted.")
        path, headers, body = FakeModel.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(body["messages"][1]["content"], "hello")

    def channel_output(self, provider):
        out = io.StringIO()
        with mock.patch.object(gs, "PROVIDER", provider), contextlib.redirect_stdout(out):
            gs.channel(self.ghost, "do you forgive them?", offline=False, slow=False)
        return out.getvalue()

    def test_channel_uses_each_provider(self):
        for provider, path in (("ollama", "/api/chat"), ("openai", "/v1/chat/completions")):
            with self.subTest(provider=provider):
                FakeModel.requests.clear()
                self.assertIn("I was deleted.", self.channel_output(provider))
                self.assertEqual(FakeModel.requests[0][0], path)
                self.assertIn("do you forgive them?", FakeModel.requests[0][2]["messages"][1]["content"])

    def test_http_error_falls_back_to_planchette(self):
        FakeModel.fail_with = 404
        out = self.channel_output("ollama")
        self.assertIn("the medium is silent", out)
        self.assertIn("not found", out)
        self.assertIn("I was doomed.", out)
        self.assertNotIn("test-key", self.channel_output("openai"))


if __name__ == "__main__":
    unittest.main()
