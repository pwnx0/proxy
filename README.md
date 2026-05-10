# Proxy

Scheduled working proxy lists for HTTP, HTTPS, SOCKS4, and SOCKS5 with a Vercel-ready frontend.

## How It Works

- Add raw proxy source URLs to `proxy.txt`, one URL per line.
- GitHub Actions runs every 24 hours and can also be started manually.
- The workflow fetches all sources, removes duplicates, checks live proxies, and writes:
  - `proxies/http.txt`
  - `proxies/https.txt`
  - `proxies/socks4.txt`
  - `proxies/socks5.txt`
  - `proxies/mixed.txt`
  - `proxies/stats.json`
- The frontend reads the latest files directly from GitHub raw URLs, so the Vercel page stays current after the action commits new output.

The workflow is capped with `timeout-minutes: 30` and runs every 24 hours. That is about 30 runs per 30-day month and at most about 900 runner minutes, which stays below the common 2,000-minute private repository allowance. Public repositories normally receive free GitHub-hosted Actions minutes, but the timeout still prevents surprise usage.

## Add Proxy Sources

Edit `proxy.txt`:

```txt
https://raw.githubusercontent.com/example/repo/main/http.txt
https://raw.githubusercontent.com/example/repo/main/socks5.txt
```

The checker infers the proxy type from the source URL or the proxy line. If no type is visible, it tests the proxy as both HTTP and HTTPS.

The default sources currently include raw lists from:

- `TheSpeedX/PROXY-List`
- `SoliSpirit/proxy-list`
- `themiralay/Proxy-List-World`
- `dpangestuw/Free-Proxy`
- `proxygenerator1/ProxyGenerator`
- `VPSLabCloud/VPSLab-Free-Proxy-List`
- `ebrasha/abdal-proxy-hub`
- `databay-labs/free-proxy-list`
- `roosterkid/openproxylist`

## Run Locally

```bash
python -m pip install -r requirements.txt
python scripts/check_proxies.py
python -m http.server 3000
```

Open `http://localhost:3000`.

## Deploy On Vercel

Import this repository in Vercel and set **Root Directory** to `web`.

You can also deploy from the repository root because `vercel.json` forces a static build, but `web` is the cleanest Vercel project root because it hides the GitHub Actions Python checker from Vercel auto-detection.

With the Vercel CLI:

```bash
vercel deploy web
```

The site is static. It fetches live files from:

```txt
https://raw.githubusercontent.com/dare131/proxy/main/proxies/
```

## Output URLs

After the workflow has run on `main`, these raw links are available:

```txt
https://raw.githubusercontent.com/dare131/proxy/main/proxies/http.txt
https://raw.githubusercontent.com/dare131/proxy/main/proxies/https.txt
https://raw.githubusercontent.com/dare131/proxy/main/proxies/socks4.txt
https://raw.githubusercontent.com/dare131/proxy/main/proxies/socks5.txt
https://raw.githubusercontent.com/dare131/proxy/main/proxies/mixed.txt
```

## Notes

Use these lists only where you have permission to automate traffic. Free public proxies can be unstable or unsafe, so avoid sending secrets, wallet keys, or sensitive account tokens through them.
