# Strictly Boolean

**Search for what you typed.**

Strictly Boolean is an open-source web search interface that treats the user's query as an instruction rather than a suggestion. It retrieves candidate pages from a search provider, independently examines those pages, and evaluates the original Boolean expression locally.

The central distinction is between **retrieval** and **verification**: the upstream search index supplies candidates; Strictly Boolean decides whether the available evidence actually satisfies the query.

## What it does

Strictly Boolean supports:

- `AND`, `OR`, and `NOT`
- implicit `AND` (`Cat Dog Frog` means `Cat AND Dog AND Frog`)
- quoted exact phrases
- parentheses and normal Boolean precedence (`NOT` > `AND` > `OR`)
- `A NOT B` as shorthand for `A AND NOT B`
- `site:` inclusion and exclusion constraints
- open-license constraints such as `license:cc-by` and `license:any-open`
- independent live-page verification
- conservative indexed-evidence fallback when a live page cannot be verified
- transparent explanations of why a result matched, failed, or could not be verified
- bounded adaptive retrieval
- concurrent live verification for faster searches
- persistent API-budget controls, per-IP rate limiting, and repeated-search caching

Results are classified as:

- **VERIFIED MATCH** — the available live evidence establishes the expression as true.
- **VERIFIED NON-MATCH** — the available evidence establishes the expression as false.
- **UNVERIFIABLE** — the available evidence is insufficient to establish either truth or falsity.
- **INDEX-ESTABLISHED MATCH / NON-MATCH** — indexed evidence is sufficient for a limited conclusion under the verifier's evidence rules.

A key rule is that **partial indexed evidence may establish presence, but absence is not inferred from a snippet**. This matters especially for `NOT`: if Strictly Boolean cannot inspect enough of a document to establish that an excluded term is absent, the result remains unverifiable.

## Architecture

```text
Boolean query
    ↓
parser / AST
    ↓
recall-oriented retrieval plan
    ↓
Brave Search candidate URLs
    ↓
URL deduplication
    ↓
live page + indexed evidence
    ↓
local evaluation of the original Boolean expression
    ↓
match / non-match / unverifiable
    ↓
transparent ranking of admitted results
```

The retrieval provider does **not** get the final say on Boolean truth. Ranking occurs after qualification and does not change the verifier's result.

## Requirements

- Python 3.8+
- a Brave Search API key

The current dependency pins preserve Python 3.8 compatibility.

## Installation

Clone the repository and create a virtual environment if desired:

```bash
git clone https://github.com/ExclusiveOranges/strictly-boolean.git
cd strictly-boolean
python -m venv .venv
```

Activate the environment, then install dependencies:

```bash
python -m pip install -r requirements.txt
```

Set your Brave API key in the environment.

Windows Command Prompt:

```bat
set BRAVE_API_KEY=YOUR_KEY_HERE
python strictlyboolean_app.py
```

PowerShell:

```powershell
$env:BRAVE_API_KEY="YOUR_KEY_HERE"
python strictlyboolean_app.py
```

macOS / Linux:

```bash
export BRAVE_API_KEY="YOUR_KEY_HERE"
python strictlyboolean_app.py
```

Then open:

```text
http://127.0.0.1:5000
```

Never commit your API key. Strictly Boolean reads `BRAVE_API_KEY` from the process environment; it is not hard-coded in the application.

## Example queries

```text
("Thomas McGuane" OR "Jim Harrison") AND Montana NOT Hemingway
```

```text
California (surfboard OR surfing) NOT (Surfline OR Pinterest)
```

```text
physics textbook site:edu license:any-open
```

```text
"College Physics" AND license:cc-by NOT site:openstax.org
```

## Candidate counts are bounded

Strictly Boolean reports how many candidates it **examined**. That number is not an estimate of every matching page on the web.

The current prototype begins with three Brave result pages per retrieval branch. If too few established matches are found, it can deepen retrieval under a hard extra-request budget. These limits exist to keep retrieval bounded and API usage predictable.

## Public-beta cost controls

Strictly Boolean uses a small local SQLite database to enforce API budgets, rate-limit search traffic, and cache completed searches. The state database is ignored by Git.

The default safeguards are deliberately finite:

- 500 Brave API requests per UTC day
- 8,000 Brave API requests per UTC month
- 60 searches per IP address per rolling hour
- 200 searches per IP address per rolling day
- 6-hour cache for identical completed searches

Every outbound Brave request is reserved against the global budget **before** it is sent. When a global limit is reached, new uncached searches stop instead of continuing to spend API requests. Cached searches can still be served.

The limits can be changed with environment variables:

```text
SB_BRAVE_DAILY_LIMIT=500
SB_BRAVE_MONTHLY_LIMIT=8000
SB_RATE_LIMIT_HOURLY=60
SB_RATE_LIMIT_DAILY=200
SB_SEARCH_CACHE_TTL_SECONDS=21600
SB_STATE_DB=strictlyboolean_state.sqlite3
SB_TRUST_PROXY=0
```

`SB_TRUST_PROXY=1` should be used only behind a trusted reverse proxy that correctly sets `X-Forwarded-For`; otherwise clients could spoof the address used for rate limiting.

The defaults are intended for a small beta, not as universal production values. Hosting environments with ephemeral or read-only filesystems should point `SB_STATE_DB` at persistent writable storage.

## License detection

License filtering requires affirmative evidence. An undetected license is not treated as "all rights reserved," and a requested license that cannot be established can make an otherwise positive result **UNVERIFIABLE**.

The prototype recognizes Creative Commons, GNU, permissive software, and open-data license families. `license:any-open` is a convenience macro for the recognized open-license set and intentionally excludes Creative Commons ND licenses.

## Optional 1980s logo

The repository does not include the optional `80s_SB_logo.png` artwork. If you have your own compatible image, place it beside `strictlyboolean_app.py`; the UI's **Logo** button can then switch between the default Courier wordmark and that local image.

`80s_SB_logo.png` is ignored by Git by default so local artwork is not accidentally published with the code.

## Development status

Strictly Boolean is currently a prototype. It now includes bounded API-budget controls, per-IP rate limiting, and repeated-search caching. Before a broad public deployment, it still needs deployment hardening, monitoring, and broader real-world testing.

The Flask development server is intended for local development, not production hosting.

## Tests

Run the dependency-free parser and repository sanity checks from the project root:

```bash
python -m unittest discover -s tests
```

The full application additionally requires the packages in `requirements.txt`.

## Contributing

Issues, test cases, reproducible retrieval failures, and pull requests are welcome. See `CONTRIBUTING.md` for the project's basic contribution principles.

## License

Strictly Boolean is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See `LICENSE`.

The AGPL is intentionally suited to network software: modified versions made available as network services must provide users access to the corresponding source as required by the license.
