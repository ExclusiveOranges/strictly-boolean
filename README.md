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
git clone <https://github.com/ExclusiveOranges/strictly-boolean.git>
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

## License detection

License filtering requires affirmative evidence. An undetected license is not treated as "all rights reserved," and a requested license that cannot be established can make an otherwise positive result **UNVERIFIABLE**.

The prototype recognizes Creative Commons, GNU, permissive software, and open-data license families. `license:any-open` is a convenience macro for the recognized open-license set and intentionally excludes Creative Commons ND licenses.

## Optional 1980s logo

The repository does not include the optional `80s_SB_logo.png` artwork. If you have your own compatible image, place it beside `strictlyboolean_app.py`; the UI's **Logo** button can then switch between the default Courier wordmark and that local image.

`80s_SB_logo.png` is ignored by Git by default so local artwork is not accidentally published with the code.

## Development status

Strictly Boolean is currently a prototype. Before a broad public deployment, the project still needs production-oriented protections such as API-budget controls, rate limiting, caching, deployment hardening, and broader real-world testing.

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
