#!/usr/bin/env python3
"""Smoke-test the packaged FastAPI dashboard routes."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "src" / "llm_memory" / "server" / "static"
DASHBOARD_DIR = ROOT / "llm-memory-dashboard"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "llm-memory-smoke/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def wait_until_ready(base_url: str) -> None:
    deadline = time.time() + 30
    last_status = None
    last_body = ""

    while time.time() < deadline:
        try:
            status, body = fetch(f"{base_url}/readyz")
            last_status = status
            last_body = body
            if status == 200:
                return
        except OSError as e:
            last_body = str(e)
        time.sleep(0.5)

    raise RuntimeError(f"server did not become ready: status={last_status}, body={last_body[:300]}")


def assert_ok(base_url: str, path: str, expected: str | None = None) -> None:
    status, body = fetch(f"{base_url}{path}")
    if status != 200:
        raise AssertionError(f"{path} returned HTTP {status}: {body[:300]}")
    if expected and expected not in body:
        raise AssertionError(f"{path} response did not contain {expected!r}")


def run_browser_smoke(base_url: str) -> None:
    node_code = """
const { chromium } = require('@playwright/test');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pages = [
    '/dashboard',
    '/dashboard/brief',
    '/dashboard/intelligence',
    '/dashboard/recall',
    '/dashboard/intents',
    '/dashboard/graph',
    '/dashboard/health',
    '/dashboard/auth',
    '/dashboard/settings',
  ];
  for (const path of pages) {
    await page.goto(process.env.LLM_MEMORY_SMOKE_URL + path, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.body.innerText.trim().length >= 20);
    const text = await page.locator('body').innerText();
    if (!text || text.length < 20) throw new Error(`${path} rendered an empty page`);
    const title = await page.title();
    console.log(`${path} ok title=${JSON.stringify(title)}`);
  }

  await page.goto(process.env.LLM_MEMORY_SMOKE_URL + '/dashboard/brief', {
    waitUntil: 'domcontentloaded',
  });
  await page.getByLabel('Task').fill('Review the authentication callback');
  await page.getByLabel('Files').fill('src/auth.py');
  await page.getByRole('button', { name: 'Prepare brief' }).click();
  await page.getByRole('heading', { name: 'Compiled context' }).waitFor({ state: 'visible' });
  await page.getByRole('heading', { name: 'Unknowns to verify' }).waitFor({ state: 'visible' });

  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of pages) {
    await page.goto(process.env.LLM_MEMORY_SMOKE_URL + path, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.body.innerText.trim().length >= 20);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth
    );
    if (overflow > 1) throw new Error(`${path} has ${overflow}px horizontal overflow at 390px`);
  }

  await page.goto(process.env.LLM_MEMORY_SMOKE_URL + '/dashboard/settings', {
    waitUntil: 'domcontentloaded',
  });
  await page.getByRole('button', { name: 'Open navigation menu' }).click();
  const drawer = page.getByRole('dialog', { name: 'Navigation menu' });
  await drawer.getByRole('link', { name: 'Memory Graph' }).waitFor({ state: 'visible' });
  await drawer.getByRole('combobox', { name: 'Project' }).waitFor({ state: 'visible' });
  await drawer.getByText('Theme', { exact: true }).waitFor({ state: 'visible' });
  await drawer.getByText('System Online', { exact: true }).waitFor({ state: 'visible' });
  await drawer.getByRole('link', { name: 'Settings' }).waitFor({ state: 'visible' });
  await drawer.getByRole('button', { name: 'Close navigation menu' }).click();
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    node_code = node_code.replace(
        "waitUntil: 'networkidle'", "waitUntil: 'domcontentloaded'"
    )
    env = {**os.environ, "LLM_MEMORY_SMOKE_URL": base_url}
    subprocess.run(["node", "-e", node_code], cwd=DASHBOARD_DIR, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Also run a real Chromium smoke test via @playwright/test.",
    )
    args = parser.parse_args()

    if not (STATIC_DIR / "index.html").exists():
        raise SystemExit(
            "Packaged dashboard static files are missing. Run python build_frontend.py first."
        )

    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="llm-memory-smoke-") as tmpdir:
        env = {
            **os.environ,
            "LLM_MEMORY_SERVER_AUTH_ENABLED": "false",
            "LLM_MEMORY_STORAGE_DATA_DIR": tmpdir,
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "llm_memory.server.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_until_ready(base_url)
            assert_ok(base_url, "/", '"status":"online"')
            assert_ok(base_url, "/dashboard", "<html")
            assert_ok(base_url, "/dashboard/recall", "<html")
            assert_ok(base_url, "/dashboard/intents", "<html")
            assert_ok(base_url, "/dashboard/graph", "<html")

            if args.browser:
                run_browser_smoke(base_url)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    print("Packaged dashboard smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
