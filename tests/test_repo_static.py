import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RepoStaticTests(unittest.TestCase):
    def test_template_uses_fixed_logo_button_label(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="logo-toggle"', html)
        self.assertIn('>Logo</button>', html)
        self.assertNotIn("toggle.textContent = '80s'", html)
        self.assertNotIn("toggle.textContent = 'Courier'", html)

    def test_courier_is_default(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const defaultVersion = 'courier-v2';", html)
        self.assertIn("let saved = 'courier';", html)

    def test_concurrent_verification_is_bounded(self):
        source = (ROOT / "strictlyboolean_app.py").read_text(encoding="utf-8")
        self.assertIn("from concurrent.futures import ThreadPoolExecutor", source)
        self.assertIn("MAX_VERIFICATION_WORKERS = 10", source)

    def test_no_hardcoded_brave_key(self):
        source = (ROOT / "strictlyboolean_web.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("BRAVE_API_KEY")', source)

    def test_budget_guard_is_wired_into_brave_requests(self):
        app_source = (ROOT / "strictlyboolean_app.py").read_text(encoding="utf-8")
        web_source = (ROOT / "strictlyboolean_web.py").read_text(encoding="utf-8")
        self.assertIn("request_guard=STATE.reserve_brave_request", app_source)
        self.assertIn("if request_guard is not None:", web_source)
        self.assertIn("request_guard()", web_source)

    def test_runtime_state_is_ignored_by_git(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("strictlyboolean_state.sqlite3", ignore)


if __name__ == "__main__":
    unittest.main()
