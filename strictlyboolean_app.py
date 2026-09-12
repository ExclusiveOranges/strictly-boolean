import re
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, send_from_directory
from urllib.parse import urlsplit

from strictlyboolean_core import parse_query
from strictlyboolean_retrieval import build_retrieval_plan
from strictlyboolean_guard import (
    BraveBudgetExceeded,
    GuardConfig,
    RateLimitExceeded,
    StateStore,
)
from strictlyboolean_web import (
    retrieve_candidates,
    evaluate_candidate_data,
    brave_search_page,
    canonicalize_url,
)

app = Flask(__name__)

GUARD_CONFIG = GuardConfig()
STATE = StateStore(GUARD_CONFIG.state_db, GUARD_CONFIG)



@app.get("/80s_SB_logo.png")
def eighties_logo():
    # The logo file is intentionally not bundled. It should already exist
    # beside strictlyboolean_app.py in the project directory.
    return send_from_directory(app.root_path, "80s_SB_logo.png")


# Retrieval starts at the long-standing three Brave pages per branch.
# If that first pass produces relatively few matches, Strictly Boolean may
# deepen the search conservatively.  The hard extra-request budget prevents a
# large OR query from multiplying API cost without bound.
INITIAL_RETRIEVAL_PAGES = 3
ADAPTIVE_MATCH_TARGET = 25
ADAPTIVE_MAX_PAGES_PER_BRANCH = 6
ADAPTIVE_MAX_EXTRA_REQUESTS = 6

# Live page fetching is I/O-bound, so verify a modest number of candidates in
# parallel. This changes wall-clock time, not Boolean truth or retrieval depth.
MAX_VERIFICATION_WORKERS = 10


LICENSE_LABELS = {
    "cc-by": "CC BY",
    "cc-by-sa": "CC BY-SA",
    "cc-by-nc": "CC BY-NC",
    "cc-by-nc-sa": "CC BY-NC-SA",
    "cc-by-nd": "CC BY-ND",
    "cc-by-nc-nd": "CC BY-NC-ND",
    "cc0": "CC0",
    "public-domain": "Public Domain",
    "gfdl": "GNU FDL",
    "gpl": "GNU GPL",
    "lgpl": "GNU LGPL",
    "agpl": "GNU AGPL",
    "mit": "MIT",
    "apache-2.0": "Apache 2.0",
    "bsd-2-clause": "BSD 2-Clause",
    "bsd-3-clause": "BSD 3-Clause",
    "isc": "ISC",
    "mpl-2.0": "MPL 2.0",
    "epl-2.0": "EPL 2.0",
    "odbl": "ODbL",
    "odc-by": "ODC-By",
    "pddl": "PDDL",
}

# Deliberately excludes ND licenses from the broad recognized-open shortcut.
OPEN_LICENSE_IDS = {
    "cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa", "cc0",
    "public-domain", "gfdl", "gpl", "lgpl", "agpl", "mit",
    "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc",
    "mpl-2.0", "epl-2.0", "odbl", "odc-by", "pddl",
}

_LICENSE_GROUP_RE = re.compile(
    r'\(\s*license:[a-z0-9.-]+(?:\s+OR\s+license:[a-z0-9.-]+)+\s*\)',
    re.I,
)
_LICENSE_TOKEN_RE = re.compile(r'(?<!\S)license:([a-z0-9.-]+)', re.I)


def _cleanup_boolean_query(value):
    value = re.sub(r'^\s*(AND|OR)\b\s*', '', value, flags=re.I)
    value = re.sub(r'\s*\b(AND|OR|NOT)\s*$', '', value, flags=re.I)
    value = re.sub(r'\(\s*\)', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def _extract_license_facets(query):
    selected = []

    def capture_group(match):
        selected.extend(re.findall(r"license:([a-z0-9.-]+)", match.group(0), re.I))
        return ' '

    query = _LICENSE_GROUP_RE.sub(capture_group, query or '')

    def capture_single(match):
        selected.append(match.group(1))
        return ' '

    query = _LICENSE_TOKEN_RE.sub(capture_single, query)
    query = _cleanup_boolean_query(query)

    expanded = set()
    for item in selected:
        key = item.lower()
        if key == 'any-open':
            expanded.update(OPEN_LICENSE_IDS)
        elif key in LICENSE_LABELS:
            expanded.add(key)
    return query, sorted(expanded), selected


def _apply_license_constraint(result, allowed_license_ids):
    if not allowed_license_ids:
        return result

    allowed = set(allowed_license_ids)
    detected = {item.get('id') for item in result.get('licenses', [])}
    detected.discard(None)

    # FALSE AND anything is still FALSE, so an already established text non-match
    # remains a non-match even if license evidence is unavailable.
    if result['status'] in {'nonmatch', 'index_nonmatch'}:
        return result

    if detected:
        matched = bool(detected & allowed)
        if matched:
            if result.get('evidence_type') == 'INDEXED EVIDENCE':
                result['evidence_type'] = 'INDEXED EVIDENCE + LICENSE METADATA'
            elif 'LICENSE' not in result.get('evidence_type', ''):
                result['evidence_type'] = result.get('evidence_type', 'LIVE DOCUMENT') + ' + LICENSE'
            result['explanation'].append('✓ accepted license detected')
            return result
        result['status'] = 'nonmatch'
        result['status_label'] = '✗ VERIFIED NON-MATCH'
        if result.get('evidence_type') == 'INDEXED EVIDENCE':
            result['evidence_type'] = 'INDEXED EVIDENCE + LICENSE METADATA'
        else:
            result['evidence_type'] = result.get('evidence_type', 'LIVE DOCUMENT') + ' + LICENSE'
        labels = ', '.join(
            item.get('label', item.get('id', '')) for item in result.get('licenses', [])
        )
        result['explanation'].append('✗ detected license not selected: ' + labels)
        return result

    # A positive text result cannot satisfy an ANDed license predicate if the
    # license is unknown. Preserve the distinction between unknown and false.
    if result['status'] in {'match', 'index_match'}:
        result['status'] = 'unknown'
        result['status_label'] = '? UNVERIFIABLE'
        result['evidence_type'] = 'LICENSE UNKNOWN'
        note = result.get('license_note')
        if note:
            result['explanation'].append('? license could not be established: ' + note)
        else:
            result['explanation'].append('? license could not be established')
    return result

STATUS_ORDER = {
    "match": 0,
    "index_match": 1,
    "unknown": 2,
    "nonmatch": 3,
    "index_nonmatch": 4,
}


def _duration_label(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def result_group(status):
    if status in {"match", "index_match"}:
        return "matches"
    if status in {"nonmatch", "index_nonmatch"}:
        return "nonmatches"
    return "unknown"


def _normalize_domain(value):
    value = (value or "").strip().lower()
    if not value:
        return ""

    if "://" in value:
        value = urlsplit(value).hostname or ""

    value = value.split("/")[0].split(":")[0].strip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


def _host_matches_domain(hostname, domain):
    hostname = (hostname or "").lower().strip(".")
    domain = _normalize_domain(domain)
    if not domain:
        return True
    return hostname == domain or hostname.endswith("." + domain)



_SITE_TOKEN_RE = re.compile(
    r'(?i)(?<!\S)(NOT\s+)?site:("([^"]+)"|[^\s()]+)'
)


def _extract_site_facets(query):
    include_sites = []
    exclude_sites = []

    def repl(match):
        is_excluded = bool(match.group(1))
        raw = match.group(3) if match.group(3) is not None else match.group(2)
        domain = _normalize_domain(raw)
        if domain:
            if is_excluded:
                exclude_sites.append(domain)
            else:
                include_sites.append(domain)
        return " "

    boolean_query = _SITE_TOKEN_RE.sub(repl, query or "")
    boolean_query = re.sub(r'^\s*(AND|OR)\b\s*', '', boolean_query, flags=re.I)
    boolean_query = re.sub(r'\s*\b(AND|OR|NOT)\s*$', '', boolean_query, flags=re.I)
    boolean_query = re.sub(r'\s+', ' ', boolean_query).strip()
    return boolean_query, include_sites, exclude_sites


def run_search(query, include_site="", exclude_site=""):
    query_without_licenses, license_filters, requested_license_tokens = _extract_license_facets(query)
    boolean_query, query_include_sites, query_exclude_sites = _extract_site_facets(query_without_licenses)

    if include_site:
        query_include_sites.append(_normalize_domain(include_site))
    if exclude_site:
        query_exclude_sites.append(_normalize_domain(exclude_site))

    query_include_sites = [x for x in query_include_sites if x]
    query_exclude_sites = [x for x in query_exclude_sites if x]

    if not boolean_query:
        raise SyntaxError("A text Boolean expression is required.")

    expression = parse_query(boolean_query)
    plan = build_retrieval_plan(expression)

    retrieval_queries = list(plan.get("queries", []))
    for site_domain in query_include_sites:
        retrieval_queries = [
            branch + " site:" + site_domain
            for branch in retrieval_queries
        ]

    empty_counts = {"matches": 0, "nonmatches": 0, "unknown": 0}

    if not plan["safe"]:
        return {
            "query": query,
            "parsed": expression.describe(),
            "plan": plan,
            "results": [],
            "counts": empty_counts,
            "retrieval_stats": None,
            "examined_candidates": 0,
            "adaptive_retrieval": None,
            "error": plan.get("reason") or "No safe retrieval plan is available.",
        }

    if not retrieval_queries:
        return {
            "query": query,
            "parsed": expression.describe(),
            "plan": plan,
            "results": [],
            "counts": empty_counts,
            "retrieval_stats": {
                "raw_candidates": 0,
                "duplicate_urls": 0,
                "unique_candidates": 0,
                "branches": [],
                "api_requests": 0,
            },
            "examined_candidates": 0,
            "adaptive_retrieval": None,
            "error": plan.get("reason"),
        }

    candidates, retrieval_stats = retrieve_candidates(
        retrieval_queries,
        verbose=False,
        return_stats=True,
        pages=INITIAL_RETRIEVAL_PAGES,
        request_guard=STATE.reserve_brave_request,
    )

    # Keep one canonical candidate object per URL so deeper retrieval can add
    # genuinely new candidates without re-fetching/re-evaluating old ones.
    candidate_by_key = {
        canonicalize_url(item.get("url", "")): item
        for item in candidates
        if item.get("url")
    }
    result_by_key = {}
    results = []

    def candidate_passes_site_constraints(candidate):
        candidate_url = candidate.get("url", "")
        candidate_host = urlsplit(candidate_url).hostname or ""

        if query_include_sites and not all(
            _host_matches_domain(candidate_host, domain)
            for domain in query_include_sites
        ):
            return False

        if query_exclude_sites and any(
            _host_matches_domain(candidate_host, domain)
            for domain in query_exclude_sites
        ):
            return False

        return True

    def evaluate_one(candidate):
        if not candidate_passes_site_constraints(candidate):
            return None

        result = evaluate_candidate_data(
            candidate,
            expression,
            discover_license=bool(license_filters),
        )
        result = _apply_license_constraint(result, license_filters)
        result["group"] = result_group(result["status"])
        domain = urlsplit(result.get("url", "")).hostname or ""
        if domain.startswith("www."):
            domain = domain[4:]
        result["domain"] = domain
        return result

    def evaluate_batch(batch):
        """Evaluate candidates concurrently while preserving input order."""
        if not batch:
            return []
        worker_count = min(MAX_VERIFICATION_WORKERS, len(batch))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            return list(executor.map(evaluate_one, batch))

    for candidate, result in zip(candidates, evaluate_batch(candidates)):
        if result is None:
            continue
        key = canonicalize_url(candidate.get("url", ""))
        result_by_key[key] = result
        results.append(result)

    def current_match_count():
        return sum(
            1 for item in results
            if result_group(item["status"]) == "matches"
        )

    adaptive = {
        "initial_pages_per_branch": INITIAL_RETRIEVAL_PAGES,
        "max_pages_per_branch": ADAPTIVE_MAX_PAGES_PER_BRANCH,
        "match_target": ADAPTIVE_MATCH_TARGET,
        "max_extra_requests": ADAPTIVE_MAX_EXTRA_REQUESTS,
        "extra_requests": 0,
        "new_unique_candidates": 0,
        "deepened": False,
        "stop_reason": None,
    }

    budget_limited = False

    if current_match_count() >= ADAPTIVE_MATCH_TARGET:
        adaptive["stop_reason"] = "match target reached in initial retrieval"
    else:
        # One additional page per branch at a time.  This keeps OR branches
        # reasonably balanced instead of spending the whole budget on branch 1.
        exhausted_branches = set()

        while (
            current_match_count() < ADAPTIVE_MATCH_TARGET
            and adaptive["extra_requests"] < ADAPTIVE_MAX_EXTRA_REQUESTS
        ):
            made_request_this_round = False

            for branch_index, branch_query in enumerate(retrieval_queries):
                if current_match_count() >= ADAPTIVE_MATCH_TARGET:
                    break
                if adaptive["extra_requests"] >= ADAPTIVE_MAX_EXTRA_REQUESTS:
                    break
                if branch_index in exhausted_branches:
                    continue

                branch_stat = retrieval_stats["branches"][branch_index]
                page_counts = branch_stat.setdefault("page_counts", [])
                next_offset = len(page_counts)

                if next_offset >= ADAPTIVE_MAX_PAGES_PER_BRANCH:
                    exhausted_branches.add(branch_index)
                    continue

                # If the prior page was empty, Brave has already told us there
                # is nothing useful to gain by asking for the next offset.
                if page_counts and page_counts[-1] == 0:
                    exhausted_branches.add(branch_index)
                    continue

                try:
                    incoming = brave_search_page(
                        branch_query,
                        next_offset,
                        request_guard=STATE.reserve_brave_request,
                    )
                except BraveBudgetExceeded:
                    adaptive["stop_reason"] = "global Brave request budget reached"
                    budget_limited = True
                    break
                made_request_this_round = True
                adaptive["deepened"] = True
                adaptive["extra_requests"] += 1

                page_counts.append(len(incoming))
                branch_stat["raw_count"] = branch_stat.get("raw_count", 0) + len(incoming)
                retrieval_stats["raw_candidates"] += len(incoming)

                if not incoming:
                    exhausted_branches.add(branch_index)
                    continue

                new_candidates = []
                for candidate in incoming:
                    url = candidate.get("url", "")
                    if not url:
                        continue
                    key = canonicalize_url(url)

                    if key in candidate_by_key:
                        existing = candidate_by_key[key]
                        for rq in candidate.get("retrieval_queries", []):
                            existing.setdefault("retrieval_queries", [])
                            if rq not in existing["retrieval_queries"]:
                                existing["retrieval_queries"].append(rq)
                        existing.setdefault("retrieval_hits", []).extend(
                            candidate.get("retrieval_hits", [])
                        )
                        continue

                    candidate_by_key[key] = candidate
                    adaptive["new_unique_candidates"] += 1
                    new_candidates.append(candidate)

                for candidate, result in zip(
                    new_candidates, evaluate_batch(new_candidates)
                ):
                    if result is None:
                        continue
                    key = canonicalize_url(candidate.get("url", ""))
                    result_by_key[key] = result
                    results.append(result)

            if budget_limited or not made_request_this_round:
                break

        if budget_limited:
            adaptive["stop_reason"] = "global Brave request budget reached"
        elif current_match_count() >= ADAPTIVE_MATCH_TARGET:
            adaptive["stop_reason"] = "match target reached"
        elif adaptive["extra_requests"] >= ADAPTIVE_MAX_EXTRA_REQUESTS:
            adaptive["stop_reason"] = "extra retrieval request budget reached"
        else:
            adaptive["stop_reason"] = "available retrieval pages exhausted"

    retrieval_stats["unique_candidates"] = len(candidate_by_key)
    retrieval_stats["duplicate_urls"] = (
        retrieval_stats["raw_candidates"] - retrieval_stats["unique_candidates"]
    )
    retrieval_stats["api_requests"] = sum(
        len(branch.get("page_counts", []))
        for branch in retrieval_stats.get("branches", [])
    )

    results.sort(
        key=lambda item: (
            STATUS_ORDER.get(item["status"], 99),
            -item["score"],
            item["title"].lower(),
        )
    )

    counts = {
        "matches": sum(1 for item in results if item["group"] == "matches"),
        "nonmatches": sum(
            1 for item in results if item["group"] == "nonmatches"
        ),
        "unknown": sum(1 for item in results if item["group"] == "unknown"),
    }

    return {
        "query": query,
        "parsed": expression.describe(),
        "plan": {
            **plan,
            "queries": retrieval_queries,
        },
        "site_filters": {
            "include": query_include_sites,
            "exclude": query_exclude_sites,
        },
        "license_filters": {
            "ids": license_filters,
            "requested": requested_license_tokens,
            "labels": [LICENSE_LABELS.get(x, x) for x in license_filters],
        },
        "results": results,
        "counts": counts,
        "retrieval_stats": retrieval_stats,
        "examined_candidates": len(results),
        "adaptive_retrieval": adaptive,
        "error": None,
    }


@app.route("/", methods=["GET"])
def index():
    query = request.args.get("q", "").strip()
    include_site = request.args.get("include_site", "").strip()
    exclude_site = request.args.get("exclude_site", "").strip()
    search = None
    error = None
    http_status = 200

    if query:
        if GUARD_CONFIG.trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For", "")
            client_ip = forwarded.split(",", 1)[0].strip() if forwarded else ""
        else:
            client_ip = request.remote_addr or ""
        client_ip = client_ip or "unknown"

        try:
            STATE.check_rate_limit(client_ip)

            cached = STATE.get_cached_search(
                query,
                include_site=include_site,
                exclude_site=exclude_site,
            )
            if cached is not None:
                search = cached["value"]
                search["cache"] = {
                    "hit": True,
                    "age_seconds": cached["age_seconds"],
                    "age_label": _duration_label(cached["age_seconds"]),
                    "ttl_seconds": GUARD_CONFIG.cache_ttl_seconds,
                    "ttl_label": _duration_label(GUARD_CONFIG.cache_ttl_seconds),
                }
                error = search.get("error")
            else:
                search = run_search(
                    query,
                    include_site=include_site,
                    exclude_site=exclude_site,
                )
                error = search.get("error")
                search["cache"] = {
                    "hit": False,
                    "age_seconds": 0,
                    "age_label": "0s",
                    "ttl_seconds": GUARD_CONFIG.cache_ttl_seconds,
                    "ttl_label": _duration_label(GUARD_CONFIG.cache_ttl_seconds),
                }
                if not error:
                    cache_value = dict(search)
                    cache_value.pop("cache", None)
                    STATE.set_cached_search(
                        query,
                        include_site,
                        exclude_site,
                        cache_value,
                    )
        except RateLimitExceeded as exc:
            minutes = max(1, (exc.retry_after_seconds + 59) // 60)
            error = (
                "Search rate limit reached for this address. "
                f"Try again in about {minutes} minute"
                f"{'s' if minutes != 1 else ''}."
            )
            http_status = 429
        except BraveBudgetExceeded as exc:
            error = (
                "Strictly Boolean has reached its global Brave API "
                f"{exc.period} request budget. Try again later."
            )
            http_status = 503
        except SyntaxError as exc:
            error = str(exc)
        except Exception as exc:
            error = f"Search failed: {exc}"

    response = render_template(
        "index.html",
        query=query,
        include_site=include_site,
        exclude_site=exclude_site,
        search=search,
        error=error,
    )
    return response, http_status


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
