"""Run PostgreSQL tests against portable binaries, bound exclusively to loopback."""
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-bin", type=Path, required=True)
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="postgres-test-", dir=ROOT / "build")).resolve()
    if not directory.is_relative_to((ROOT / "build").resolve()):
        raise ValueError("Percorso test fuori da build/")
    suffix = ".exe" if os.name == "nt" else ""
    def tool(name):
        return str(args.postgres_bin.resolve() / (name + suffix))
    password = secrets.token_hex(24)
    password_file = directory / "password"
    password_file.write_text(password, encoding="ascii")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    data = directory / "data"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    subprocess.run([tool("initdb"), "-D", str(data), "-U", "remotedesk_test", "-E", "UTF8",
                    "--auth=scram-sha-256", "--pwfile", str(password_file)], check=True, **flags)
    password_file.unlink()
    started = False
    try:
        subprocess.run([tool("pg_ctl"), "start", "-D", str(data), "-l", str(directory / "postgres.log"),
                        "-o", f"-h 127.0.0.1 -p {port}", "-w"], check=True, **flags)
        started = True
        environment = dict(os.environ, PGPASSWORD=password)
        subprocess.run([tool("createdb"), "-h", "127.0.0.1", "-p", str(port), "-U", "remotedesk_test",
                        "remotedesk_test"], env=environment, check=True, **flags)
        environment["REMOTEDESK_TEST_DATABASE_URL"] = f"postgresql://remotedesk_test:{password}@127.0.0.1:{port}/remotedesk_test"
        # No credentials are printed. Tests create/drop only randomly named isolated schemas.
        result = subprocess.run([sys.executable, "-m", "unittest", "tests.test_postgres", "-v"],
                                cwd=ROOT, env=environment, capture_output=True, text=True, **flags)
        print(result.stdout, end="")
        print(result.stderr, end="")
        return result.returncode
    finally:
        if started:
            subprocess.run([tool("pg_ctl"), "stop", "-D", str(data), "-m", "fast", "-w"], check=True, **flags)
        print(f"PostgreSQL di test arrestato. Log locali in {directory}")


if __name__ == "__main__":
    raise SystemExit(main())
