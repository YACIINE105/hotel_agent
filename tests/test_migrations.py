import os
import subprocess
import sys


def test_migrations_upgrade_to_head(db_url):
    env = {**os.environ, "DATABASE_URL": db_url}
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env,
                            capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert result.returncode == 0, result.stderr
    check = subprocess.run([sys.executable, "-m", "alembic", "check"], env=env,
                           capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert check.returncode == 0, check.stdout + check.stderr  # models and migrations agree
