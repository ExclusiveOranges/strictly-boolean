from dataclasses import dataclass

from strictlyboolean_core import (
    TermNode,
    NotNode,
    AndNode,
    OrNode,
)


MAX_RETRIEVAL_BRANCHES = 16
MAX_INTERMEDIATE_CLAUSES = 256


@dataclass(frozen=True)
class Literal:
    value: str
    phrase: bool
    negative: bool = False

    def atom_key(self):
        return (
            self.value.lower(),
            self.phrase,
        )

    def retrieval_text(self):
        if self.phrase:
            return f'"{self.value}"'

        return self.value


class RetrievalPlanError(Exception):
    pass


def _combine_and(
    left_clauses,
    right_clauses,
):
    """
    Cartesian product for DNF AND.
    """

    projected_size = (
        len(left_clauses)
        * len(right_clauses)
    )

    if (
        projected_size
        > MAX_INTERMEDIATE_CLAUSES
    ):
        raise RetrievalPlanError(
            "Boolean expression expands "
            "into too many intermediate "
            "retrieval clauses."
        )

    combined = []

    for left in left_clauses:
        for right in right_clauses:
            combined.append(
                left + right
            )

    return combined


def _to_dnf(
    node,
    negated=False,
):
    """
    Convert the Boolean AST to DNF.

    Returns a list of clauses.

    Each clause is a list of Literals.

    NOT is pushed all the way down to
    individual terms using De Morgan's laws.
    """

    if isinstance(node, TermNode):
        return [[
            Literal(
                value=node.value,
                phrase=node.phrase,
                negative=negated,
            )
        ]]

    if isinstance(node, NotNode):
        return _to_dnf(
            node.child,
            not negated,
        )

    if isinstance(node, AndNode):
        if not negated:
            left = _to_dnf(
                node.left,
                False,
            )

            right = _to_dnf(
                node.right,
                False,
            )

            return _combine_and(
                left,
                right,
            )

        # NOT (A AND B)
        #
        # becomes:
        #
        # NOT A OR NOT B

        left = _to_dnf(
            node.left,
            True,
        )

        right = _to_dnf(
            node.right,
            True,
        )

        clauses = left + right

        if (
            len(clauses)
            > MAX_INTERMEDIATE_CLAUSES
        ):
            raise RetrievalPlanError(
                "Boolean expression expands "
                "into too many intermediate "
                "retrieval clauses."
            )

        return clauses

    if isinstance(node, OrNode):
        if not negated:
            left = _to_dnf(
                node.left,
                False,
            )

            right = _to_dnf(
                node.right,
                False,
            )

            clauses = left + right

            if (
                len(clauses)
                > MAX_INTERMEDIATE_CLAUSES
            ):
                raise RetrievalPlanError(
                    "Boolean expression expands "
                    "into too many intermediate "
                    "retrieval clauses."
                )

            return clauses

        # NOT (A OR B)
        #
        # becomes:
        #
        # NOT A AND NOT B

        left = _to_dnf(
            node.left,
            True,
        )

        right = _to_dnf(
            node.right,
            True,
        )

        return _combine_and(
            left,
            right,
        )

    raise TypeError(
        f"Unknown node type: {type(node)}"
    )


def _remove_duplicate_literals(
    clause
):
    result = []
    seen = set()

    for literal in clause:
        key = (
            literal.atom_key(),
            literal.negative,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(literal)

    return result


def _clause_is_contradiction(
    clause
):
    """
    A AND NOT A can never match.

    Such a clause can safely be discarded.
    """

    positive = set()
    negative = set()

    for literal in clause:
        key = literal.atom_key()

        if literal.negative:
            negative.add(key)
        else:
            positive.add(key)

    return bool(
        positive & negative
    )


def _positive_branch(
    clause
):
    """
    Drop negative literals only AFTER
    converting the complete Boolean
    expression to DNF.
    """

    return [
        literal.retrieval_text()
        for literal in clause
        if not literal.negative
    ]


def _dedupe_branches(
    branches
):
    unique = []
    seen = set()

    for branch in branches:
        # Remove repeated search terms
        # while preserving order.

        cleaned = []
        local_seen = set()

        for item in branch:
            key = item.lower()

            if key in local_seen:
                continue

            local_seen.add(key)
            cleaned.append(item)

        key = tuple(
            item.lower()
            for item in cleaned
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(cleaned)

    return unique


def _remove_subsumed_branches(
    branches
):
    """
    If we already search:

        A

    then there is no retrieval reason
    to separately search:

        A B

    because A is the broader candidate
    search.

    This is retrieval optimization only.
    The original Boolean AST still decides
    whether each page actually matches.
    """

    ordered = sorted(
        branches,
        key=len,
    )

    kept = []

    for branch in ordered:
        branch_set = {
            item.lower()
            for item in branch
        }

        subsumed = False

        for existing in kept:
            existing_set = {
                item.lower()
                for item in existing
            }

            if existing_set.issubset(
                branch_set
            ):
                subsumed = True
                break

        if not subsumed:
            kept.append(branch)

    return kept


def build_retrieval_plan(
    expression
):
    """
    Compile an expression into simple
    positive candidate-retrieval searches.

    Returns a dictionary:

        {
            "safe": True/False,
            "queries": [...],
            "dnf_clause_count": N,
            "reason": None/string
        }
    """

    try:
        clauses = _to_dnf(
            expression
        )

    except RetrievalPlanError as error:
        return {
            "safe": False,
            "queries": [],
            "dnf_clause_count": 0,
            "reason": str(error),
        }

    usable_clauses = []

    for clause in clauses:
        clause = (
            _remove_duplicate_literals(
                clause
            )
        )

        if _clause_is_contradiction(
            clause
        ):
            continue

        usable_clauses.append(
            clause
        )

    # Expression may be logically
    # impossible, e.g. A AND NOT A.

    if not usable_clauses:
        return {
            "safe": True,
            "queries": [],
            "dnf_clause_count": 0,
            "reason":
                "The Boolean expression "
                "contains no satisfiable "
                "retrieval clauses.",
        }

    branches = []

    for clause in usable_clauses:
        branch = _positive_branch(
            clause
        )

        # A satisfiable clause containing
        # only negative literals has no
        # positive anchor.
        #
        # Example:
        #
        #     NOT Hemingway
        #
        # or:
        #
        #     A OR NOT B
        #
        # One branch would require searching
        # essentially the entire web.

        if not branch:
            return {
                "safe": False,
                "queries": [],
                "dnf_clause_count":
                    len(usable_clauses),
                "reason":
                    "At least one valid Boolean "
                    "branch contains only negative "
                    "conditions and therefore has "
                    "no safe positive retrieval "
                    "anchor.",
            }

        branches.append(branch)

    branches = _dedupe_branches(
        branches
    )

    branches = _remove_subsumed_branches(
        branches
    )

    if (
        len(branches)
        > MAX_RETRIEVAL_BRANCHES
    ):
        return {
            "safe": False,
            "queries": [],
            "dnf_clause_count":
                len(usable_clauses),
            "reason":
                "Boolean expression requires "
                f"{len(branches)} retrieval "
                "branches, exceeding the "
                f"configured maximum of "
                f"{MAX_RETRIEVAL_BRANCHES}.",
        }

    queries = [
        " ".join(branch)
        for branch in branches
    ]

    return {
        "safe": True,
        "queries": queries,
        "dnf_clause_count":
            len(usable_clauses),
        "reason": None,
    }