import os
import re
import html
import json
import warnings
from urllib.parse import urlsplit, urlunsplit, urljoin

import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from strictlyboolean_core import (
    Truth,
    TermNode,
    NotNode,
    AndNode,
    OrNode,
    normalize_text,
    term_present,
    explanation_lines,
    partial_explanation_lines,
)

warnings.filterwarnings(
    "ignore",
    category=MarkupResemblesLocatorWarning,
)

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY")
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
RESULTS_PER_PAGE = 20
SEARCH_PAGES = 3
FETCH_TIMEOUT = 12

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "gift",
    "amp",
    "amp;",
    "output",
}


def canonicalize_url(url):
    """Conservative URL canonicalization for deduplication."""
    try:
        url = html.unescape(url or "")
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        hostname = (parts.hostname or "").lower()

        if hostname.startswith("www."):
            hostname = hostname[4:]

        port = parts.port
        if (
            port
            and not (scheme == "http" and port == 80)
            and not (scheme == "https" and port == 443)
        ):
            netloc = f"{hostname}:{port}"
        else:
            netloc = hostname

        path = parts.path or "/"
        if path != "/":
            path = path.rstrip("/")

        kept_parameters = []
        if parts.query:
            for item in parts.query.split("&"):
                if not item:
                    continue
                parameter_name = item.split("=", 1)[0].lower()
                if not parameter_name:
                    continue
                if parameter_name in TRACKING_PARAMETERS:
                    continue
                kept_parameters.append(item)

        kept_parameters.sort()
        query = "&".join(kept_parameters)

        return urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return url


def deduplicate_candidates(candidates):
    """Deduplicate URLs while preserving retrieval provenance."""
    unique_by_url = {}
    order = []

    for candidate in candidates:
        url = candidate.get("url", "")
        if not url:
            continue

        key = canonicalize_url(url)

        if key not in unique_by_url:
            copied = dict(candidate)
            copied["retrieval_queries"] = list(
                candidate.get("retrieval_queries", [])
            )
            copied["retrieval_hits"] = list(
                candidate.get("retrieval_hits", [])
            )
            unique_by_url[key] = copied
            order.append(key)
            continue

        existing = unique_by_url[key]

        for query in candidate.get("retrieval_queries", []):
            if query not in existing["retrieval_queries"]:
                existing["retrieval_queries"].append(query)

        existing.setdefault("retrieval_hits", []).extend(
            candidate.get("retrieval_hits", [])
        )

    return [unique_by_url[key] for key in order]


def provenance_lines(candidate):
    queries = candidate.get("retrieval_queries", [])

    if not queries:
        return []

    if len(queries) == 1:
        return [f"  Found via: {queries[0]}"]

    lines = [f"  Found via {len(queries)} retrieval branches:"]
    lines.extend(f"    - {query}" for query in queries)
    return lines


def brave_search_page(query, offset):
    if not BRAVE_API_KEY:
        raise RuntimeError(
            "BRAVE_API_KEY environment variable is not set."
        )

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY,
    }

    params = {
        "q": query,
        "count": RESULTS_PER_PAGE,
        "offset": offset,
        "spellcheck": "false",
        "extra_snippets": "true",
    }

    response = requests.get(
        BRAVE_SEARCH_URL,
        headers=headers,
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    results = data.get("web", {}).get("results", [])

    candidates = []
    for rank, result in enumerate(results, start=1):
        candidates.append(
            {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "description": result.get("description", ""),
                "extra_snippets": result.get("extra_snippets", []) or [],
                "retrieval_queries": [query],
                "retrieval_hits": [
                    {
                        "query": query,
                        "offset": offset,
                        "rank": rank,
                    }
                ],
            }
        )

    return candidates


def retrieve_candidates(queries, verbose=True, return_stats=False, pages=SEARCH_PAGES):
    raw_candidates = []
    branch_stats = []

    if verbose:
        print("\nExecuting retrieval plan:")

    for branch_number, query in enumerate(queries, start=1):
        if verbose:
            print(f"\nBranch {branch_number}: {query}")

        page_counts = []

        for offset in range(pages):
            candidates = brave_search_page(query, offset)
            page_counts.append(len(candidates))

            if verbose:
                print(
                    f"  Brave page {offset + 1}: "
                    f"{len(candidates)} candidates"
                )

            raw_candidates.extend(candidates)
            if not candidates:
                break

        branch_stats.append(
            {
                "query": query,
                "page_counts": page_counts,
                "raw_count": sum(page_counts),
            }
        )

    unique_candidates = deduplicate_candidates(raw_candidates)
    duplicate_count = len(raw_candidates) - len(unique_candidates)

    stats = {
        "raw_candidates": len(raw_candidates),
        "duplicate_urls": duplicate_count,
        "unique_candidates": len(unique_candidates),
        "branches": branch_stats,
        "api_requests": sum(len(branch["page_counts"]) for branch in branch_stats),
    }

    if verbose:
        print()
        print(f"Raw candidates:       {stats['raw_candidates']}")
        print(f"Duplicate URLs:       {stats['duplicate_urls']}")
        print(f"Unique candidates:    {stats['unique_candidates']}")

    if return_stats:
        return unique_candidates, stats

    return unique_candidates


LICENSE_DEFINITIONS = [
    ("cc-by-nc-nd", "CC BY-NC-ND", [
        r"creativecommons\.org/licenses/by-nc-nd/", r"\bcc by-nc-nd\b", r"creative commons attribution-noncommercial-noderivatives"
    ]),
    ("cc-by-nc-sa", "CC BY-NC-SA", [
        r"creativecommons\.org/licenses/by-nc-sa/", r"\bcc by-nc-sa\b", r"creative commons attribution-noncommercial-sharealike"
    ]),
    ("cc-by-nc", "CC BY-NC", [
        r"creativecommons\.org/licenses/by-nc/", r"\bcc by-nc(?!-)\b", r"creative commons attribution-noncommercial(?!-(?:sharealike|noderivatives))"
    ]),
    ("cc-by-nd", "CC BY-ND", [
        r"creativecommons\.org/licenses/by-nd/", r"\bcc by-nd\b", r"creative commons attribution-noderivatives"
    ]),
    ("cc-by-sa", "CC BY-SA", [
        r"creativecommons\.org/licenses/by-sa/", r"\bcc by-sa\b", r"creative commons attribution-sharealike"
    ]),
    ("cc-by", "CC BY", [
        r"creativecommons\.org/licenses/by/", r"\bcc by(?!-(?:sa|nc|nd))(?: \d(?:\.\d)?)?\b", r"creative commons attribution(?!-(?:sharealike|noncommercial|noderivatives))(?: license)?"
    ]),
    ("cc0", "CC0", [
        r"creativecommons\.org/publicdomain/zero/", r"\bcc0(?: 1\.0)?\b", r"creative commons zero"
    ]),
    ("public-domain", "Public Domain", [
        r"creativecommons\.org/publicdomain/mark/", r"\bpublic domain mark\b", r"dedicated to the public domain"
    ]),
    ("gfdl", "GNU FDL", [
        r"gnu\.org/(?:copyleft/)?fdl", r"gnu free documentation license", r"\bgfdl\b"
    ]),
    ("gpl", "GNU GPL", [
        r"gnu\.org/licenses/(?:old-licenses/)?gpl", r"gnu general public license", r"\bgpl[- ]?[23](?:\.0)?\b"
    ]),
    ("lgpl", "GNU LGPL", [
        r"gnu\.org/licenses/(?:old-licenses/)?lgpl", r"gnu lesser general public license", r"\blgpl[- ]?[23](?:\.0)?\b"
    ]),
    ("agpl", "GNU AGPL", [
        r"gnu\.org/licenses/agpl", r"gnu affero general public license", r"\bagpl[- ]?3(?:\.0)?\b"
    ]),
    ("mit", "MIT", [
        r"opensource\.org/(?:license|licenses)/mit", r"spdx\.org/licenses/mit", r"\bmit license\b"
    ]),
    ("apache-2.0", "Apache 2.0", [
        r"apache\.org/licenses/license-2\.0", r"apache license,? version 2\.0", r"spdx\.org/licenses/apache-2\.0"
    ]),
    ("bsd-2-clause", "BSD 2-Clause", [
        r"bsd 2-clause", r"spdx\.org/licenses/bsd-2-clause"
    ]),
    ("bsd-3-clause", "BSD 3-Clause", [
        r"bsd 3-clause", r"spdx\.org/licenses/bsd-3-clause"
    ]),
    ("isc", "ISC", [
        r"\bisc license\b", r"spdx\.org/licenses/isc"
    ]),
    ("mpl-2.0", "MPL 2.0", [
        r"mozilla public license(?:,? version)? 2\.0", r"mozilla\.org/MPL/2\.0", r"spdx\.org/licenses/mpl-2\.0"
    ]),
    ("epl-2.0", "EPL 2.0", [
        r"eclipse public license(?:,? version)? 2\.0", r"spdx\.org/licenses/epl-2\.0"
    ]),
    ("odbl", "ODbL", [
        r"opendatacommons\.org/licenses/odbl", r"open database license", r"\bodbl\b"
    ]),
    ("odc-by", "ODC-By", [
        r"opendatacommons\.org/licenses/by/", r"open data commons attribution license", r"\bodc-by\b"
    ]),
    ("pddl", "PDDL", [
        r"opendatacommons\.org/licenses/pddl", r"public domain dedication and license", r"\bpddl\b"
    ]),
]


def detect_licenses(soup, raw_html, return_resolution=False):
    """Detect and adjudicate affirmative license evidence.

    v1.4.4 deliberately separates *finding* license-looking evidence from
    deciding which license governs the result page/resource.  A page can mention
    many licenses (resource lists, guides, related works, site footers).  We keep
    the strongest evidence for each license and only establish a license when a
    single license wins at the strongest evidence tier.

    With ``return_resolution=False`` this remains backward-compatible and
    returns the established license list.  With ``return_resolution=True`` it
    also returns candidate evidence and an ambiguity note.
    """
    signals = []

    def add_matches(value, source, strength):
        text = html.unescape(str(value or "")).strip()
        if not text:
            return

        for license_id, label, patterns in LICENSE_DEFINITIONS:
            for pattern in patterns:
                match = re.search(pattern, text, re.I)
                if match:
                    signals.append({
                        "id": license_id,
                        "label": label,
                        "evidence": match.group(0)[:140],
                        "source": source,
                        "strength": int(strength),
                    })
                    break

    def rel_contains_license(tag):
        rel = tag.get("rel", []) or []
        if isinstance(rel, str):
            rel = rel.split()
        return any(str(item).lower() == "license" for item in rel)

    def inside_footer(tag):
        try:
            return tag.name == "footer" or tag.find_parent("footer") is not None
        except Exception:
            return False

    # Machine-readable resource-level fields are strongest.
    for tag in soup.find_all(["a", "link"], href=True):
        if rel_contains_license(tag):
            strength = 48 if inside_footer(tag) else 92
            add_matches(tag.get("href", ""), "rel=license URL", strength)
            add_matches(tag.get_text(" ", strip=True), "rel=license text", strength)

    for tag in soup.find_all(attrs={"itemprop": re.compile(r"^license$", re.I)}):
        value = (
            tag.get("href")
            or tag.get("content")
            or tag.get("value")
            or tag.get_text(" ", strip=True)
        )
        add_matches(value, "itemprop=license", 100)

    for tag in soup.find_all("meta"):
        field_values = [
            str(tag.get(key, "")).strip()
            for key in ("name", "property", "itemprop")
            if str(tag.get(key, "")).strip()
        ]
        if any(
            re.search(r"(?:^|[:._-])(license|licence|rights)(?:$|[:._-])", value, re.I)
            for value in field_values
        ):
            add_matches(tag.get("content", ""), "license metadata", 100)

    # JSON-LD license properties are strong, but multiple different license
    # values at this tier are intentionally treated as ambiguous later.
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

        def walk_license_value(value):
            if isinstance(value, dict):
                for child in value.values():
                    if isinstance(child, (str, int, float)):
                        add_matches(child, "JSON-LD license", 100)
                    else:
                        walk_license_value(child)
            elif isinstance(value, list):
                for child in value:
                    walk_license_value(child)
            elif isinstance(value, (str, int, float)):
                add_matches(value, "JSON-LD license", 100)

        def walk_json(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() == "license":
                        walk_license_value(child)
                    elif isinstance(child, (dict, list)):
                        walk_json(child)
            elif isinstance(value, list):
                for child in value:
                    walk_json(child)

        walk_json(data)

    # Explicit assignment language outranks generic links/badges.  Scope
    # matters: a resource statement in main/article content beats a different
    # site-wide footer statement.
    assignment_patterns = [
        r"(?:this|the)\s+(?:work|book|textbook|resource|content|site|website|page|software|code|dataset)\b.{0,120}?\b(?:is\s+)?licensed\s+under\b.{0,180}",
        r"(?:this|the)\s+(?:work|book|textbook|resource|content|site|website|page|software|code|dataset)\b.{0,120}?\buses\s+the\b.{0,180}?\blicense\b",
        r"\breleased\s+under\b.{0,180}",
        r"\bdistributed\s+under\b.{0,180}",
        r"\blicensed\s+under\b.{0,180}",
    ]

    def add_assignment_matches(text, source, strength):
        text = html.unescape(str(text or ""))
        for pattern in assignment_patterns:
            for match in re.finditer(pattern, text, re.I):
                add_matches(match.group(0), source, strength)

    primary_container = soup.find("article") or soup.find("main")
    if primary_container is not None:
        add_assignment_matches(
            primary_container.get_text(" ", strip=True),
            "explicit license statement in primary content",
            96,
        )
    else:
        # No explicit main/article container: inspect body after stripping
        # navigation/footer/aside so site chrome cannot tie resource evidence.
        try:
            body_copy = BeautifulSoup(str(soup.body or soup), "html.parser")
            for tag in body_copy.find_all(["nav", "footer", "aside"]):
                tag.decompose()
            add_assignment_matches(
                body_copy.get_text(" ", strip=True),
                "explicit license statement in page content",
                92,
            )
        except Exception:
            pass

    for footer in soup.find_all("footer"):
        add_assignment_matches(
            footer.get_text(" ", strip=True),
            "explicit license statement in footer",
            50,
        )

    # License/copyright/rights-scoped elements.  Footer-only signals remain
    # usable, but lose to resource-specific metadata or assignment statements.
    scoped = []
    for tag in soup.find_all(True):
        marker = " ".join([
            str(tag.get("id", "")),
            " ".join(tag.get("class", []) or []),
        ])
        if tag.name == "footer" or re.search(r"license|licence|copyright|rights", marker, re.I):
            scoped.append(tag)

    for tag in scoped:
        strength = 45 if inside_footer(tag) else 78
        add_matches(tag.get_text(" ", strip=True), "license-scoped text", strength)
        for link in tag.find_all("a", href=True):
            add_matches(link.get("href", ""), "license-scoped link", strength)

    # Direct canonical license links can be affirmative, but are weaker than
    # metadata or an explicit assignment because resource-list pages often link
    # to several licenses for several different works.
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if re.search(
            r"creativecommons\.org/(?:licenses|publicdomain)/|gnu\.org/(?:licenses|copyleft)/|"
            r"opensource\.org/(?:license|licenses)/|spdx\.org/licenses/|"
            r"apache\.org/licenses/|opendatacommons\.org/licenses/",
            href,
            re.I,
        ):
            strength = 45 if inside_footer(link) else 68
            add_matches(href, "canonical license link", strength)

    cc_badge_re = re.compile(
        r"^\s*CC\s+(?:BY(?:-(?:NC|ND|SA)){0,3}|0)(?:\s+\d(?:\.\d)?)?\s*$",
        re.I,
    )
    for tag in soup.find_all(["a", "span", "p", "div", "small"]):
        text = tag.get_text(" ", strip=True)
        if text and len(text) <= 40 and cc_badge_re.match(text):
            strength = 45 if inside_footer(tag) else 72
            add_matches(text, "standalone CC license label", strength)

    # Keep the strongest signal for each license ID.
    strongest_by_id = {}
    for signal in signals:
        license_id = signal["id"]
        current = strongest_by_id.get(license_id)
        if current is None or signal["strength"] > current["strength"]:
            strongest_by_id[license_id] = signal

    candidates = sorted(
        strongest_by_id.values(),
        key=lambda item: (-item["strength"], item["label"]),
    )

    established = []
    note = None
    if candidates:
        top_strength = candidates[0]["strength"]
        top = [item for item in candidates if item["strength"] == top_strength]
        if len(top) == 1:
            established = [top[0]]
        else:
            labels = ", ".join(item["label"] for item in top)
            note = "conflicting equally strong license evidence: " + labels

    resolution = {
        "licenses": established,
        "candidates": candidates,
        "ambiguous": bool(note),
        "note": note,
    }
    if return_resolution:
        return resolution
    return established



def _same_host(left_url, right_url):
    left = (urlsplit(left_url).hostname or "").lower()
    right = (urlsplit(right_url).hostname or "").lower()
    if left.startswith("www."):
        left = left[4:]
    if right.startswith("www."):
        right = right[4:]
    return bool(left and right and left == right)


def _license_probe_urls(page_url, soup, raw_html):
    """Return a small, ordered set of same-site pages likely to carry license data.

    This is deliberately conservative. A license-filtered search may inspect one
    or two closely related pages (license/attribution/preface) when the landing
    page itself is a thin JavaScript shell. It does not crawl the site generally.
    """
    ranked = []
    seen = set()

    def add(candidate, score):
        candidate = html.unescape(str(candidate or "")).strip()
        if not candidate:
            return
        absolute = urljoin(page_url, candidate)
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"}:
            return
        if not _same_host(page_url, absolute):
            return
        clean = parts._replace(fragment="").geturl()
        if clean == page_url or clean in seen:
            return
        seen.add(clean)
        ranked.append((score, clean))

    keyword_scores = [
        (r"license|licence|licensing", 100),
        (r"attribution|copyright|rights|permissions|reuse", 90),
        (r"preface", 80),
        (r"about[-_/ ]?(?:this[-_/ ]?)?(?:book|resource)", 70),
    ]

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        text = link.get_text(" ", strip=True)
        haystack = (href + " " + text).lower()
        for pattern, score in keyword_scores:
            if re.search(pattern, haystack, re.I):
                add(href, score)
                break

    # Some JS shells contain useful route strings only inside serialized state.
    # Harvest same-origin absolute URLs and route-like strings, but only when
    # their path strongly suggests license/attribution/preface material.
    raw = html.unescape(raw_html or "")
    raw = raw.replace(r"\/", "/")
    for match in re.finditer(r'https?://[^"\'<>\\s]+', raw, re.I):
        value = match.group(0)
        low = value.lower()
        for pattern, score in keyword_scores:
            if re.search(pattern, low, re.I):
                add(value, score - 5)
                break

    # OpenStax's /details/books/<slug> landing pages are intentionally thin;
    # the corresponding Preface is the book-level page that explicitly states
    # the license. Keep this resolver narrow and transparent rather than
    # pretending the landing-page HTML contained evidence that it did not.
    parts = urlsplit(page_url)
    host = (parts.hostname or "").lower()
    path_match = re.fullmatch(r"/details/books/([^/?#]+)/?", parts.path)
    if host.endswith("openstax.org") and path_match:
        slug = path_match.group(1)
        add(f"/books/{slug}/pages/preface", 120)

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, url in ranked[:3]]


def discover_related_licenses(page_url, soup, raw_html):
    """Probe at most two closely related same-site pages for adjudicated license evidence."""
    probe_urls = _license_probe_urls(page_url, soup, raw_html)

    for probe_url in probe_urls[:2]:
        try:
            response = requests.get(
                probe_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                timeout=min(FETCH_TIMEOUT, 10),
                allow_redirects=True,
            )
        except requests.RequestException:
            continue

        if response.status_code != 200:
            continue
        if "html" not in response.headers.get("Content-Type", "").lower():
            continue

        try:
            related_soup = BeautifulSoup(response.text, "html.parser")
        except Exception:
            continue

        resolution = detect_licenses(
            related_soup,
            response.text,
            return_resolution=True,
        )
        discovered = []
        for item in resolution.get("licenses", []):
            copied = dict(item)
            copied["source"] = "related page: " + probe_url + " — " + item.get("source", "license evidence")
            discovered.append(copied)

        if discovered:
            return {
                "licenses": discovered,
                "note": None,
                "probe_url": probe_url,
            }

        if resolution.get("ambiguous"):
            return {
                "licenses": [],
                "note": "related page " + probe_url + ": " + resolution.get("note", "ambiguous license evidence"),
                "probe_url": probe_url,
            }

    return {"licenses": [], "note": None, "probe_url": None}

def extract_document(url, discover_license=False):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as error:
        return {"ok": False, "reason": str(error)}

    if response.status_code != 200:
        return {
            "ok": False,
            "reason": f"HTTP {response.status_code}",
            "status_code": response.status_code,
        }

    content_type = response.headers.get("Content-Type", "").lower()
    if "html" not in content_type:
        return {
            "ok": False,
            "reason": (
                "Retrieved content is not HTML "
                f"({content_type or 'unknown'})"
            ),
        }

    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as error:
        return {
            "ok": False,
            "reason": f"Could not parse HTML: {error}",
        }

    license_resolution = detect_licenses(
        soup,
        response.text,
        return_resolution=True,
    )
    detected_licenses = list(license_resolution.get("licenses", []))
    license_note = license_resolution.get("note")

    if discover_license and not detected_licenses and not license_note:
        related_resolution = discover_related_licenses(
            response.url,
            soup,
            response.text,
        )
        detected_licenses = list(related_resolution.get("licenses", []))
        license_note = related_resolution.get("note")

    html_title = ""
    if soup.title:
        html_title = soup.title.get_text(" ", strip=True)

    meta_description = ""
    meta = soup.find(
        "meta",
        attrs={"name": re.compile("^description$", re.I)},
    )
    if meta and meta.get("content"):
        meta_description = meta.get("content", "")

    for tag in soup.find_all(
        ["script", "style", "noscript", "nav", "footer", "aside"]
    ):
        tag.decompose()

    main_content = soup.find("article") or soup.find("main") or soup.body
    if main_content:
        body_text = main_content.get_text(" ", strip=True)
    else:
        body_text = soup.get_text(" ", strip=True)

    combined = " ".join([html_title, meta_description, body_text])

    return {
        "ok": True,
        "document": normalize_text(combined),
        "html_title": html_title,
        "final_url": response.url,
        "licenses": detected_licenses,
        "license_note": license_note,
    }


TITLE_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "into",
    "about",
    "your",
    "you",
    "are",
    "was",
    "were",
    "has",
    "have",
    "had",
    "not",
    "but",
    "its",
    "his",
    "her",
    "our",
    "their",
}


def meaningful_title_words(title):
    words = re.findall(r"[A-Za-z0-9]+", title.lower())
    return {
        word
        for word in words
        if len(word) >= 3 and word not in TITLE_STOPWORDS
    }


def document_confidence(candidate, page):
    document = page["document"]

    if len(document) < 500:
        return False, "Retrieved page contains very little readable content"

    brave_words = meaningful_title_words(candidate.get("title", ""))
    if not brave_words:
        return True, None

    comparison_text = normalize_text(
        page.get("html_title", "") + " " + document[:2000]
    )

    hits = sum(
        1
        for word in brave_words
        if re.search(
            r"(?<!\w)" + re.escape(word) + r"(?!\w)",
            comparison_text,
        )
    )

    overlap_ratio = hits / len(brave_words)

    if overlap_ratio < 0.30:
        return (
            False,
            "Retrieved page does not appear to match Brave's indexed result",
        )

    return True, None


def clean_indexed_piece(text):
    """Convert Brave snippet markup to rendered plain text."""
    if not text:
        return ""

    text = html.unescape(text)

    # Avoid asking BeautifulSoup to parse ordinary plain text unless
    # there is actually markup to remove.
    if "<" not in text and ">" not in text:
        return text

    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(" ", strip=True)


def build_indexed_evidence(candidate):
    pieces = [
        candidate.get("title", ""),
        candidate.get("description", ""),
    ]
    pieces.extend(candidate.get("extra_snippets", []))

    cleaned = [clean_indexed_piece(piece) for piece in pieces if piece]
    return normalize_text(" ".join(cleaned))


def positive_atoms(node, negated=False):
    if isinstance(node, TermNode):
        if negated:
            return []
        return [(node.value, node.phrase)]

    if isinstance(node, NotNode):
        return positive_atoms(node.child, not negated)

    if isinstance(node, (AndNode, OrNode)):
        return positive_atoms(node.left, negated) + positive_atoms(
            node.right, negated
        )

    return []


def dedupe_atoms(atoms):
    result = []
    seen = set()

    for value, phrase in atoms:
        key = (normalize_text(value), phrase)
        if key in seen:
            continue
        seen.add(key)
        result.append((value, phrase))

    return result


def calculate_rank_score(
    candidate,
    expression,
    document="",
    html_title="",
):
    """Transparent ranking. Ranking never changes Boolean truth."""
    atoms = dedupe_atoms(positive_atoms(expression))
    title = normalize_text(html_title or candidate.get("title", ""))
    document = normalize_text(document)

    score = 0
    reasons = []

    for value, phrase in atoms:
        in_title = term_present(value, title, phrase=phrase)
        in_document = term_present(value, document, phrase=phrase)

        if phrase:
            if in_title:
                score += 10
                reasons.append(f'+10 phrase "{value}" in title')
            if in_document:
                score += 3
                reasons.append(f'+3 phrase "{value}" in document')
        else:
            if in_title:
                score += 5
                reasons.append(f"+5 term {value} in title")
            if in_document:
                score += 1
                reasons.append(f"+1 term {value} in document")

    branch_count = len(candidate.get("retrieval_queries", []))
    if branch_count:
        branch_points = branch_count * 2
        score += branch_points
        reasons.append(
            f"+{branch_points} found via {branch_count} retrieval branch"
            + ("es" if branch_count != 1 else "")
        )

    return score, reasons



# ============================================================
# UI LOGIC EXPLANATIONS — v1.2.1
# ============================================================

def _ui_live_logic_lines(node, text):
    """Concise Boolean proof for browser presentation.

    AND nodes are structural and intentionally flattened. OR and NOT
    remain visible because they materially explain why a branch passed
    or failed. The underlying evaluator is unchanged.
    """
    if isinstance(node, TermNode):
        present = term_present(node.value, text, phrase=node.phrase)
        symbol = "✓" if present else "✗"
        state = "present" if present else "absent"
        return [f"{symbol} {node.describe()} {state}"]

    if isinstance(node, NotNode):
        if isinstance(node.child, TermNode):
            child_present = term_present(
                node.child.value, text, phrase=node.child.phrase
            )
            if child_present:
                return [f"✗ {node.child.describe()} present — NOT condition failed"]
            return [f"✓ {node.child.describe()} absent — NOT condition satisfied"]

        lines = _ui_live_logic_lines(node.child, text)
        passed = node.evaluate(text)
        lines.append(
            "✓ NOT condition satisfied" if passed
            else "✗ NOT condition failed"
        )
        return lines

    if isinstance(node, AndNode):
        return (
            _ui_live_logic_lines(node.left, text)
            + _ui_live_logic_lines(node.right, text)
        )

    if isinstance(node, OrNode):
        lines = (
            _ui_live_logic_lines(node.left, text)
            + _ui_live_logic_lines(node.right, text)
        )
        lines.append(
            "✓ OR condition satisfied" if node.evaluate(text)
            else "✗ OR condition failed"
        )
        return lines

    return []


def ui_live_explanation_lines(expression, text):
    lines = _ui_live_logic_lines(expression, text)
    lines.append(
        "✓ Boolean expression satisfied" if expression.evaluate(text)
        else "✗ Boolean expression not satisfied"
    )
    return lines


def _ui_partial_logic_lines(node, text):
    """Concise proof for incomplete indexed evidence."""
    truth = node.evaluate_partial(text)

    if isinstance(node, TermNode):
        if truth == Truth.TRUE:
            return [f"✓ {node.describe()} present in indexed evidence"]
        return [f"? {node.describe()} not established"]

    if isinstance(node, NotNode):
        if isinstance(node.child, TermNode):
            child_truth = node.child.evaluate_partial(text)
            if child_truth == Truth.TRUE:
                return [
                    f"✗ {node.child.describe()} present in indexed evidence — NOT disproved"
                ]
            return [
                f"? {node.child.describe()} absence cannot be established from indexed evidence"
            ]

        lines = _ui_partial_logic_lines(node.child, text)
        label = {
            Truth.TRUE: "✓ NOT condition established",
            Truth.FALSE: "✗ NOT condition disproved",
            Truth.UNKNOWN: "? NOT condition unresolved",
        }[truth]
        lines.append(label)
        return lines

    if isinstance(node, AndNode):
        return (
            _ui_partial_logic_lines(node.left, text)
            + _ui_partial_logic_lines(node.right, text)
        )

    if isinstance(node, OrNode):
        lines = (
            _ui_partial_logic_lines(node.left, text)
            + _ui_partial_logic_lines(node.right, text)
        )
        label = {
            Truth.TRUE: "✓ OR condition established",
            Truth.FALSE: "✗ OR condition disproved",
            Truth.UNKNOWN: "? OR condition unresolved",
        }[truth]
        lines.append(label)
        return lines

    return []


def ui_partial_explanation_lines(expression, text):
    truth = expression.evaluate_partial(text)
    lines = _ui_partial_logic_lines(expression, text)
    final = {
        Truth.TRUE: "✓ Boolean expression established by indexed evidence",
        Truth.FALSE: "✗ Boolean expression disproved by indexed evidence",
        Truth.UNKNOWN: "? Boolean expression unresolved",
    }[truth]
    lines.append(final)
    return lines

def _base_result(candidate):
    return {
        "candidate": candidate,
        "title": candidate.get("title", "") or candidate.get("url", ""),
        "url": candidate.get("url", ""),
        "description": clean_indexed_piece(candidate.get("description", "")),
        "retrieval_queries": list(candidate.get("retrieval_queries", [])),
        "status": "unknown",
        "status_label": "? UNVERIFIABLE",
        "evidence_type": "UNKNOWN",
        "score": 0,
        "score_reasons": [],
        "explanation": [],
        "live_failure_reason": None,
        "indexed_extra_snippets": 0,
        "final_url": None,
        "licenses": [],
        "license_note": None,
    }


def recover_from_index_data(
    candidate,
    expression,
    live_failure_reason,
    licenses=None,
    final_url=None,
    license_note=None,
):
    result_data = _base_result(candidate)
    result_data["live_failure_reason"] = live_failure_reason
    result_data["licenses"] = list(licenses or [])
    result_data["license_note"] = license_note
    result_data["final_url"] = final_url

    indexed_text = build_indexed_evidence(candidate)
    if not indexed_text:
        return result_data

    truth = expression.evaluate_partial(indexed_text)
    extra_count = len(candidate.get("extra_snippets", []))

    result_data["evidence_type"] = "INDEXED EVIDENCE"
    result_data["indexed_extra_snippets"] = extra_count
    result_data["explanation"] = ui_partial_explanation_lines(
        expression, indexed_text
    )

    if truth == Truth.TRUE:
        result_data["status"] = "index_match"
        result_data["status_label"] = "≈ INDEX-ESTABLISHED MATCH"
    elif truth == Truth.FALSE:
        result_data["status"] = "index_nonmatch"
        result_data["status_label"] = "≈ INDEX-ESTABLISHED NON-MATCH"

    score, reasons = calculate_rank_score(
        candidate,
        expression,
        document=indexed_text,
        html_title=candidate.get("title", ""),
    )
    result_data["score"] = score
    result_data["score_reasons"] = reasons
    return result_data


def evaluate_candidate_data(candidate, expression, discover_license=False):
    """Evaluate one candidate and return structured data without printing."""
    if discover_license:
        page = extract_document(
            candidate.get("url", ""),
            discover_license=True,
        )
    else:
        page = extract_document(
            candidate.get("url", ""),
        )

    if not page["ok"]:
        return recover_from_index_data(
            candidate,
            expression,
            page["reason"],
        )

    confident, reason = document_confidence(candidate, page)
    if not confident:
        return recover_from_index_data(
            candidate,
            expression,
            reason,
            licenses=page.get("licenses", []),
            final_url=page.get("final_url"),
            license_note=page.get("license_note"),
        )

    document = page["document"]
    matched = expression.evaluate(document)
    result_data = _base_result(candidate)
    result_data["status"] = "match" if matched else "nonmatch"
    result_data["status_label"] = (
        "✓ VERIFIED MATCH" if matched else "✗ VERIFIED NON-MATCH"
    )
    result_data["evidence_type"] = "LIVE DOCUMENT"
    result_data["final_url"] = page.get("final_url")
    result_data["licenses"] = list(page.get("licenses", []))
    result_data["license_note"] = page.get("license_note")
    result_data["explanation"] = ui_live_explanation_lines(
        expression, document
    )

    score, reasons = calculate_rank_score(
        candidate,
        expression,
        document=document,
        html_title=page.get("html_title", ""),
    )
    result_data["score"] = score
    result_data["score_reasons"] = reasons
    return result_data


def print_evaluation_result(number, result):
    candidate = result["candidate"]
    print(f"{number}. {result['title']}")
    print(result["url"])

    for line in provenance_lines(candidate):
        print(line)

    if result.get("live_failure_reason"):
        print(f"  Live page: {result['live_failure_reason']}")
        if result["evidence_type"] == "INDEXED EVIDENCE":
            print(
                "  Indexed evidence: title + description + "
                f"{result['indexed_extra_snippets']} extra snippet(s)"
            )

    print(result["status_label"])

    if result["evidence_type"] == "LIVE DOCUMENT":
        print("  Evidence: LIVE DOCUMENT")

    if result["explanation"]:
        print()
        heading = (
            "  Partial-evidence logic:"
            if result["evidence_type"] == "INDEXED EVIDENCE"
            else "  Why:"
        )
        print(heading)
        for line in result["explanation"]:
            print(f"    {line}")


def evaluate_candidate(number, candidate, expression, verbose=True):
    """Backward-compatible console wrapper around structured evaluation."""
    result = evaluate_candidate_data(candidate, expression)
    if verbose:
        print_evaluation_result(number, result)
    return result
