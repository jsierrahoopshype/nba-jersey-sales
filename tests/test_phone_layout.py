"""Phone layout of the list tables in a real browser (Playwright via node). Skips when unavailable."""
import functools
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def playwright_ok():
    if not shutil.which("node"):
        return False
    return subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


@unittest.skipUnless(playwright_ok(), "needs node and Playwright")
class PhoneLayout(unittest.TestCase):
    def test_list_tables(self):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=os.path.join(ROOT, "docs"))
        handler.log_message = lambda *a: None
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as srv:
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                res = subprocess.run(["node", os.path.join(ROOT, "tests", "phone_layout.js"), "http://127.0.0.1:%d/" % srv.server_address[1]],
                                     capture_output=True, text=True, timeout=300)
            finally:
                srv.shutdown()
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = json.loads(res.stdout.strip().splitlines()[-1])
        for r in rows:
            self.assertFalse(r["overflow"], r)
            self.assertEqual(r["errors"], 0, r)
            if "dxVisible" in r:
                self.assertEqual(r["dxVisible"], r["vw"] > 640, r)
                self.assertFalse(r["xrowVisible"], r)
            if "afterTap" in r:
                self.assertTrue(r["afterTap"], r)
                self.assertFalse(r["overflowAfterTap"], r)
        self.assertTrue(any("afterTap" in r for r in rows))


if __name__ == "__main__":
    unittest.main()
