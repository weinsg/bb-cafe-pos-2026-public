"""Secrets come only from the environment (.env); a server refuses to start without them."""
import os
import pathlib
import re
import secrets
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def start(env_overrides):
    """Import the app the way uvicorn does, with a clean environment plus the given values."""
    env = {key: value for key, value in os.environ.items()
           if key not in ("SECRET_KEY", "ADMIN_PASSWORD", "APP_ENV")}
    env.update(env_overrides)
    env.setdefault("DATABASE_URL", "sqlite://")
    return subprocess.run([sys.executable, "-c", "import app.main"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


class TestSecretsOnlyInEnv(unittest.TestCase):
    def test_server_refuses_to_start_without_its_own_secrets(self):
        good_key = secrets.token_hex(32)
        for overrides, message in (
            ({"APP_ENV": "production"}, "SECRET_KEY must be set"),
            ({"APP_ENV": "production", "SECRET_KEY": "too-short", "ADMIN_PASSWORD": "x"}, "SECRET_KEY must be set"),
            ({"APP_ENV": "production", "SECRET_KEY": good_key}, "ADMIN_PASSWORD must be set"),
        ):
            result = start(overrides)
            self.assertNotEqual(result.returncode, 0, overrides)
            self.assertIn(message, result.stderr)
        ok = start({"APP_ENV": "production", "SECRET_KEY": good_key, "ADMIN_PASSWORD": secrets.token_hex(8)})
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_laptop_without_env_uses_a_random_key_never_one_from_the_code(self):
        result = start({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("random development key", result.stderr)
        keys = {subprocess.run([sys.executable, "-c", "import app.auth as a; print(a.SECRET_KEY)"], cwd=ROOT,
                               env={k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "APP_ENV")},
                               capture_output=True, text=True, timeout=120).stdout.strip() for _ in range(2)}
        self.assertEqual(len(keys), 2, "each laptop run must get a different random key")

    def test_no_secret_values_in_docker_files(self):
        compose_files = ["docker-compose.yml", "docker-compose.prod.yml",
                         *(str(p.relative_to(ROOT)) for p in ROOT.glob("deliverables/**/compose*.yml"))]
        for name in compose_files:
            text = (ROOT / name).read_text()
            for line in text.splitlines():
                if any(word in line for word in ("PASSWORD", "SECRET_KEY", "DATABASE_URL")) and ":" in line:
                    # Every secret is a ${VARIABLE:?...} reference, never a written value.
                    self.assertIn("${", line, f"{name}: {line.strip()}")
                    # An empty default (${X:-}) writes nothing; a default with a value would be a written secret.
                    self.assertIsNone(re.search(r"\$\{\w+:-[^}]+\}", line), f"{name}: no default value allowed: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
