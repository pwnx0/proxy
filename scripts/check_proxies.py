from __future__ import annotations

import argparse
import asyncio
import random
import ipaddress
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import aiohttp
from aiohttp import ClientTimeout
from aiohttp_socks import ProxyConnector


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILE = ROOT / "proxy.txt"
OUTPUT_DIR = ROOT / "proxies"
SOURCE_OUTPUT_DIR = OUTPUT_DIR / "source"
FETCH_TIMEOUT = int(os.getenv("FETCH_TIMEOUT", "20"))
CHECK_TIMEOUT = int(os.getenv("CHECK_TIMEOUT", "8"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "250"))
MAX_PROXIES_PER_RUN = int(os.getenv("MAX_PROXIES_PER_RUN", "50000"))
MIN_SUCCESSES = max(1, int(os.getenv("MIN_SUCCESSES", "3")))
SAMPLE_SEED = os.getenv("SAMPLE_SEED") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
USER_AGENT = "dare131-proxy-checker/1.0"


def env_urls(name: str, default: str) -> list[str]:
    seen = set()
    urls = []
    for url in os.getenv(name, default).split(","):
        normalized = url.strip()
        if normalized and normalized not in seen:
            urls.append(normalized)
            seen.add(normalized)
    return urls


HTTP_TEST_URLS = env_urls(
    "HTTP_TEST_URLS",
    "http://www.gstatic.com/generate_204,http://detectportal.firefox.com/success.txt,"
    "http://captive.apple.com,http://neverssl.com,http://example.com",
)
HTTPS_TEST_URLS = env_urls(
    "HTTPS_TEST_URLS",
    "https://www.gstatic.com/generate_204,https://www.cloudflare.com/cdn-cgi/trace,"
    "https://1.1.1.1/cdn-cgi/trace,https://api.ipify.org?format=json,https://example.com",
)
BOT_TEST_URLS = env_urls("BOT_TEST_URLS", "")


PROXY_PATTERN = re.compile(
    r"(?:(?P<scheme>https?|socks4|socks5)://)?"
    r"(?:(?P<user>[A-Za-z0-9._~%!$&'()*+,;=-]+):(?P<password>[^@\s]+)@)?"
    r"(?P<host>(?:\d{1,3}\.){3}\d{1,3})"
    r":(?P<port>\d{2,5})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    kind: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def address(self) -> str:
        auth = ""
        if self.username:
            auth = self.username
            if self.password:
                auth += f":{self.password}"
            auth += "@"
        return f"{auth}{self.host}:{self.port}"

    @property
    def output_line(self) -> str:
        return self.address

    def proxy_url(self) -> str:
        scheme = "http" if self.kind in {"http", "https"} else self.kind
        return f"{scheme}://{self.address}"


def source_urls(path: Path) -> list[str]:
    urls = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def infer_kinds(line: str, source_url: str) -> list[str]:
    lowered = f"{source_url} {line}".lower()
    if line.lower().startswith("socks4://") or "socks4" in lowered:
        return ["socks4"]
    if line.lower().startswith("socks5://") or "socks5" in lowered:
        return ["socks5"]
    if line.lower().startswith("https://") or re.search(r"(^|[/_.-])(https|ssl)([/_.-]|$)", lowered):
        return ["https"]
    if line.lower().startswith("http://") or re.search(r"(^|[/_.-])http([/_.-]|$)", lowered):
        return ["http"]
    return ["http", "https"]


def parse_candidates(text: str, source_url: str) -> set[Candidate]:
    candidates: set[Candidate] = set()
    for match in PROXY_PATTERN.finditer(text):
        host = match.group("host")
        port = int(match.group("port"))
        if not 1 <= port <= 65535:
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            continue
        scheme = (match.group("scheme") or "").lower()
        kinds = [scheme] if scheme in {"http", "https", "socks4", "socks5"} else infer_kinds(match.group(0), source_url)
        for kind in kinds:
            candidates.add(
                Candidate(
                    kind=kind,
                    host=host,
                    port=port,
                    username=match.group("user"),
                    password=match.group("password"),
                )
            )
    return candidates


async def fetch_source(session: aiohttp.ClientSession, url: str) -> tuple[str, str]:
    try:
        async with session.get(url, timeout=ClientTimeout(total=FETCH_TIMEOUT)) as response:
            if response.status >= 400:
                print(f"skip source {url}: HTTP {response.status}")
                return url, ""
            return url, await response.text(errors="ignore")
    except Exception as exc:
        print(f"skip source {url}: {exc}")
        return url, ""


async def load_candidates(urls: Iterable[str]) -> list[Candidate]:
    timeout = ClientTimeout(total=FETCH_TIMEOUT)
    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        fetched = await asyncio.gather(*(fetch_source(session, url) for url in urls))

    found: set[Candidate] = set()
    for url, text in fetched:
        found.update(parse_candidates(text, url))

    ordered = sorted(found, key=lambda item: (item.kind, item.host, item.port, item.username or ""))
    if len(ordered) <= MAX_PROXIES_PER_RUN:
        return ordered

    categories = ["http", "https", "socks4", "socks5"]
    groups: dict[str, list[Candidate]] = {kind: [] for kind in categories}
    for candidate in ordered:
        groups.setdefault(candidate.kind, []).append(candidate)

    for kind, values in groups.items():
        random.Random(f"{SAMPLE_SEED}:{kind}").shuffle(values)

    per_kind = max(1, MAX_PROXIES_PER_RUN // len(categories))
    selected: list[Candidate] = []
    selected_set: set[Candidate] = set()
    for kind in categories:
        for candidate in groups.get(kind, [])[:per_kind]:
            selected.append(candidate)
            selected_set.add(candidate)

    remainder = [candidate for values in groups.values() for candidate in values]
    random.Random(f"{SAMPLE_SEED}:remainder").shuffle(remainder)
    for candidate in remainder:
        if len(selected) >= MAX_PROXIES_PER_RUN:
            break
        if candidate not in selected_set:
            selected.append(candidate)
            selected_set.add(candidate)

    limited = sorted(selected, key=lambda item: (item.kind, item.host, item.port, item.username or ""))
    print(f"limiting candidates from {len(ordered)} to {len(limited)} with balanced type sampling seed {SAMPLE_SEED}")
    return limited


async def check_with_session(
    session: aiohttp.ClientSession,
    target_urls: list[str],
    *,
    proxy_url: str | None = None,
    require_bot_success: bool = False,
) -> bool:
    successes = 0
    bot_success = not require_bot_success
    for index, target_url in enumerate(target_urls):
        try:
            async with session.get(target_url, proxy=proxy_url, allow_redirects=False) as response:
                if 200 <= response.status < 400:
                    successes += 1
                    if target_url in BOT_TEST_URLS:
                        bot_success = True
                    if successes >= MIN_SUCCESSES and bot_success:
                        return True
        except Exception:
            pass
        remaining = len(target_urls) - index - 1
        if successes + remaining < MIN_SUCCESSES:
            return False
    return successes >= MIN_SUCCESSES and bot_success


async def check_http(candidate: Candidate) -> bool:
    target_urls = BOT_TEST_URLS + HTTPS_TEST_URLS if candidate.kind == "https" else HTTP_TEST_URLS
    timeout = ClientTimeout(total=CHECK_TIMEOUT, connect=CHECK_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=1, ssl=False)
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            return await check_with_session(
                session,
                target_urls,
                proxy_url=candidate.proxy_url(),
                require_bot_success=candidate.kind == "https" and bool(BOT_TEST_URLS),
            )
    except Exception:
        return False


async def check_socks(candidate: Candidate) -> bool:
    timeout = ClientTimeout(total=CHECK_TIMEOUT, connect=CHECK_TIMEOUT)
    target_urls = BOT_TEST_URLS + HTTPS_TEST_URLS
    try:
        # Plain SOCKS4 (unlike SOCKS4a/SOCKS5) mostly can't resolve hostnames
        # server-side. Forcing rdns=True here made hostname-based checks fail
        # against otherwise-working SOCKS4 proxies. Only request remote DNS
        # resolution for protocols that reliably support it.
        rdns = candidate.kind != "socks4"
        connector = ProxyConnector.from_url(candidate.proxy_url(), rdns=rdns)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            return await check_with_session(
                session,
                target_urls,
                require_bot_success=bool(BOT_TEST_URLS),
            )
    except Exception:
        return False


async def check_all(candidates: list[Candidate]) -> dict[str, list[str]]:
    results: dict[str, list[str]] = defaultdict(list)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def worker(candidate: Candidate) -> None:
        async with semaphore:
            ok = await (check_socks(candidate) if candidate.kind.startswith("socks") else check_http(candidate))
            if ok:
                results[candidate.kind].append(candidate.output_line)

    await asyncio.gather(*(worker(candidate) for candidate in candidates))
    return {kind: sorted(set(values)) for kind, values in results.items()}


def write_outputs(results: dict[str, list[str]], total_candidates: int) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    categories = ["http", "https", "socks4", "socks5"]
    mixed = sorted(set().union(*(set(results.get(kind, [])) for kind in categories)))

    for kind in categories:
        (OUTPUT_DIR / f"{kind}.txt").write_text("\n".join(results.get(kind, [])) + ("\n" if results.get(kind) else ""), encoding="utf-8")
    (OUTPUT_DIR / "mixed.txt").write_text("\n".join(mixed) + ("\n" if mixed else ""), encoding="utf-8")

    stats = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "totalCandidates": total_candidates,
        "working": {kind: len(results.get(kind, [])) for kind in categories},
        "mixed": len(mixed),
        "settings": {
            "concurrency": CONCURRENCY,
            "checkTimeoutSeconds": CHECK_TIMEOUT,
            "maxProxiesPerRun": MAX_PROXIES_PER_RUN,
            "minSuccesses": MIN_SUCCESSES,
            "sampleSeed": SAMPLE_SEED,
            "httpTestUrls": HTTP_TEST_URLS,
            "httpsTestUrls": HTTPS_TEST_URLS,
            "botTestUrls": BOT_TEST_URLS,
        },
    }
    (OUTPUT_DIR / "stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")


def write_source_candidates(candidates: list[Candidate]) -> dict[str, int]:
    SOURCE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    categories = ["http", "https", "socks4", "socks5"]
    by_kind: dict[str, list[str]] = {kind: [] for kind in categories}
    for candidate in candidates:
        if candidate.kind in by_kind:
            by_kind[candidate.kind].append(candidate.output_line)

    counts = {}
    for kind in categories:
        values = sorted(set(by_kind[kind]))
        counts[kind] = len(values)
        (SOURCE_OUTPUT_DIR / f"{kind}.txt").write_text("\n".join(values) + ("\n" if values else ""), encoding="utf-8")
    mixed = sorted(set().union(*(set(by_kind[kind]) for kind in categories)))
    counts["mixed"] = len(mixed)
    (SOURCE_OUTPUT_DIR / "mixed.txt").write_text("\n".join(mixed) + ("\n" if mixed else ""), encoding="utf-8")
    return counts


async def async_main(args: argparse.Namespace) -> None:
    urls = source_urls(args.sources)
    if not urls:
        raise SystemExit("proxy.txt does not contain any source URLs")
    candidates = await load_candidates(urls)
    print(f"loaded {len(candidates)} candidates from {len(urls)} sources")
    source_counts = write_source_candidates(candidates)
    print("source candidates:", source_counts)
    if not candidates:
        raise SystemExit("No proxy candidates were loaded. Check source URLs or GitHub raw network access.")
    results = await check_all(candidates)
    write_outputs(results, len(candidates))
    print("working:", {key: len(value) for key, value in results.items()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch source proxy lists, test them, and write working outputs.")
    parser.add_argument("--sources", type=Path, default=SOURCE_FILE)
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
