import argparse
import json
import os
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


DEFAULT_USER_AGENT = "TeamWriterFetcher/1.0 (+https://github.com/daravenrk/TeamWriter)"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify_url(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.netloc}_{parsed.path}".strip("_") or "document"
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)[:80]


def scrape_allowed(url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    try:
        parser.set_url(robots_url)
        parser.read()
        return parser.can_fetch(user_agent, url)
    except Exception:
        # If robots cannot be retrieved, default to allow and let caller decide policy.
        return True


def extract_text_from_html(html: str, selector: str | None = None) -> tuple[str, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else None

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    root = None
    if selector:
        root = soup.select_one(selector)
    if root is None:
        root = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup

    text = "\n".join(line.strip() for line in root.get_text("\n").splitlines() if line.strip())
    return text, title


def try_fetch(url: str, timeout: int, user_agent: str, verify_ssl: bool) -> dict | None:
    headers = {"User-Agent": user_agent, "Accept": "application/json,text/plain,text/markdown,application/xml,text/xml,text/html;q=0.8,*/*;q=0.5"}
    response = requests.get(url, headers=headers, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()

    content_type = (response.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        parsed = response.json()
        return {
            "mode": "fetch",
            "title": None,
            "content": json.dumps(parsed, ensure_ascii=True, indent=2),
            "content_type": content_type,
        }

    if "text/plain" in content_type or "text/markdown" in content_type:
        return {
            "mode": "fetch",
            "title": None,
            "content": response.text,
            "content_type": content_type,
        }

    if "xml" in content_type:
        soup = BeautifulSoup(response.text, "xml")
        items = soup.find_all(["item", "entry"])
        lines = []
        for item in items[:100]:
            title = (item.find("title").get_text(strip=True) if item.find("title") else "")
            summary_tag = item.find("description") or item.find("summary")
            summary = summary_tag.get_text(strip=True) if summary_tag else ""
            lines.append(f"- {title}\n  {summary}".strip())
        content = "\n".join(lines).strip() or response.text
        return {
            "mode": "fetch",
            "title": None,
            "content": content,
            "content_type": content_type,
        }

    # HTML needs scraping for meaningful content extraction.
    return None


def scrape_page(url: str, timeout: int, user_agent: str, selector: str | None, verify_ssl: bool) -> dict:
    headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    response = requests.get(url, headers=headers, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()
    text, title = extract_text_from_html(response.text, selector)
    return {
        "mode": "scrape",
        "title": title,
        "content": text,
        "content_type": (response.headers.get("content-type") or "").lower(),
    }


def write_outputs(out_dir: str, url: str, result: dict) -> dict:
    # Scraper analytics: log each fetch/scrape attempt.
    analytics_path = os.path.join(out_dir, "research_analytics.jsonl")
    analytics_event = {
        "timestamp": now_utc(),
        "url": url,
        "mode": result["mode"],
        "content_type": result.get("content_type"),
        "title": result.get("title"),
        "status": "success",
    }
    try:
        with open(analytics_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(analytics_event) + "\n")
    except Exception:
        pass
    os.makedirs(out_dir, exist_ok=True)
    base_name = f"{now_utc()}_{slugify_url(url)}_{result['mode']}"
    text_path = os.path.join(out_dir, f"{base_name}.txt")
    meta_path = os.path.join(out_dir, f"{base_name}.json")

    with open(text_path, "w", encoding="utf-8") as text_file:
        text_file.write(result["content"])

    metadata = {
        "url": url,
        "saved_at_utc": now_utc(),
        "mode": result["mode"],
        "title": result.get("title"),
        "content_type": result.get("content_type"),
        "text_file": text_path,
    }
    with open(meta_path, "w", encoding="utf-8") as meta_file:
        json.dump(metadata, meta_file, ensure_ascii=True, indent=2)

    return {"text_file": text_path, "meta_file": meta_path, **metadata}


def process_url(
    url: str,
    out_dir: str,
    mode: str,
    allow_scrape: bool,
    timeout: int,
    selector: str | None,
    user_agent: str,
    verify_ssl: bool,
) -> dict:
    if mode not in {"auto", "fetch", "scrape"}:
        raise ValueError("mode must be one of: auto, fetch, scrape")

    if mode in {"auto", "fetch"}:
        fetch_result = try_fetch(url=url, timeout=timeout, user_agent=user_agent, verify_ssl=verify_ssl)
        if fetch_result is not None:
            saved = write_outputs(out_dir, url, fetch_result)
            saved["strategy"] = "fetch"
            return saved
        if mode == "fetch":
            raise RuntimeError("fetch mode could not extract structured content; URL appears to require scraping")

    if not allow_scrape:
        raise RuntimeError("scraping disabled and fetch strategy did not produce extractable content")
    if not scrape_allowed(url, user_agent):
        raise RuntimeError("robots.txt disallows scraping for this URL and user-agent")

    scraped = scrape_page(url=url, timeout=timeout, user_agent=user_agent, selector=selector, verify_ssl=verify_ssl)
    saved = write_outputs(out_dir, url, scraped)
    saved["strategy"] = "scrape"
    return saved


class FetcherHandler(BaseHTTPRequestHandler):
    default_out_dir = os.environ.get("FETCHER_OUTPUT_DIR", "/app/research")
    default_timeout = int(os.environ.get("FETCHER_TIMEOUT_SECONDS", "30"))
    default_user_agent = os.environ.get("FETCHER_USER_AGENT", DEFAULT_USER_AGENT)
    default_verify_ssl = os.environ.get("FETCHER_VERIFY_SSL", "true").lower() not in {"0", "false", "no"}

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/fetch":
            self._send_json(404, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8") or "{}")
            url = payload["url"]
            result = process_url(
                url=url,
                out_dir=payload.get("out_dir") or self.default_out_dir,
                mode=payload.get("mode", "auto"),
                allow_scrape=payload.get("allow_scrape", True),
                timeout=int(payload.get("timeout", self.default_timeout)),
                selector=payload.get("selector"),
                user_agent=payload.get("user_agent") or self.default_user_agent,
                verify_ssl=payload.get("verify_ssl", self.default_verify_ssl),
            )
            self._send_json(200, {"status": "ok", "result": result})
        except KeyError:
            self._send_json(400, {"status": "error", "error": "missing required field: url"})
        except Exception as exc:
            self._send_json(400, {"status": "error", "error": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TeamWriter fetcher/scraper")
    parser.add_argument("--url", help="URL to process")
    parser.add_argument("--out-dir", default=os.environ.get("FETCHER_OUTPUT_DIR", "/app/research"), help="Output directory")
    parser.add_argument("--mode", choices=["auto", "fetch", "scrape"], default="auto", help="Extraction strategy")
    parser.add_argument("--selector", default=None, help="Optional CSS selector for scrape mode")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("FETCHER_TIMEOUT_SECONDS", "30")), help="HTTP timeout in seconds")
    parser.add_argument("--user-agent", default=os.environ.get("FETCHER_USER_AGENT", DEFAULT_USER_AGENT), help="HTTP user-agent")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--no-scrape", action="store_true", help="Disable scrape fallback")
    parser.add_argument("--serve", action="store_true", help="Run as HTTP service")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address for service mode")
    parser.add_argument("--port", type=int, default=11999, help="Port for service mode")
    return parser.parse_args()


def run_service(bind: str, port: int) -> None:
    server = ThreadingHTTPServer((bind, port), FetcherHandler)
    print(f"Fetcher service listening on http://{bind}:{port}")
    server.serve_forever()


def main() -> int:
    args = parse_args()

    if args.serve:
        run_service(args.bind, args.port)
        return 0

    if not args.url:
        print("error: --url is required when not running --serve")
        return 2

    result = process_url(
        url=args.url,
        out_dir=args.out_dir,
        mode=args.mode,
        allow_scrape=not args.no_scrape,
        timeout=args.timeout,
        selector=args.selector,
        user_agent=args.user_agent,
        verify_ssl=not args.insecure,
    )
    print(json.dumps({"status": "ok", "result": result}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
