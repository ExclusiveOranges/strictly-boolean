import re
from enum import Enum


# ============================================================
# THREE-VALUED LOGIC
# ============================================================

class Truth(Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


def truth_not(value):
    if value == Truth.TRUE:
        return Truth.FALSE

    if value == Truth.FALSE:
        return Truth.TRUE

    return Truth.UNKNOWN


def truth_and(left, right):
    if left == Truth.FALSE or right == Truth.FALSE:
        return Truth.FALSE

    if left == Truth.TRUE and right == Truth.TRUE:
        return Truth.TRUE

    return Truth.UNKNOWN


def truth_or(left, right):
    if left == Truth.TRUE or right == Truth.TRUE:
        return Truth.TRUE

    if left == Truth.FALSE and right == Truth.FALSE:
        return Truth.FALSE

    return Truth.UNKNOWN


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(text):
    text = text or ""
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def term_present(value, text, phrase=False):
    value = normalize_text(value)

    if phrase:
        return value in text

    pattern = (
        r"(?<!\w)"
        + re.escape(value)
        + r"(?!\w)"
    )

    return bool(
        re.search(pattern, text)
    )


# ============================================================
# TOKENS
# ============================================================

TOKEN_TERM = "TERM"
TOKEN_PHRASE = "PHRASE"
TOKEN_AND = "AND"
TOKEN_OR = "OR"
TOKEN_NOT = "NOT"
TOKEN_LPAREN = "LPAREN"
TOKEN_RPAREN = "RPAREN"
TOKEN_EOF = "EOF"


def tokenize(query):
    tokens = []
    position = 0
    length = len(query)

    while position < length:
        char = query[position]

        if char.isspace():
            position += 1
            continue

        if char == "(":
            tokens.append(
                (TOKEN_LPAREN, char)
            )
            position += 1
            continue

        if char == ")":
            tokens.append(
                (TOKEN_RPAREN, char)
            )
            position += 1
            continue

        if char == '"':
            position += 1
            start = position

            while (
                position < length
                and query[position] != '"'
            ):
                position += 1

            if position >= length:
                raise SyntaxError(
                    "Unclosed quotation mark."
                )

            phrase = query[
                start:position
            ].strip()

            if not phrase:
                raise SyntaxError(
                    "Empty quoted phrase."
                )

            tokens.append(
                (TOKEN_PHRASE, phrase)
            )

            position += 1
            continue

        start = position

        while (
            position < length
            and not query[position].isspace()
            and query[position]
            not in '()"'
        ):
            position += 1

        word = query[
            start:position
        ]

        upper_word = word.upper()

        if upper_word == "AND":
            tokens.append(
                (TOKEN_AND, word)
            )

        elif upper_word == "OR":
            tokens.append(
                (TOKEN_OR, word)
            )

        elif upper_word == "NOT":
            tokens.append(
                (TOKEN_NOT, word)
            )

        else:
            tokens.append(
                (TOKEN_TERM, word)
            )

    tokens.append(
        (TOKEN_EOF, "")
    )

    return tokens


# ============================================================
# AST NODES
# ============================================================

class TermNode:
    def __init__(
        self,
        value,
        phrase=False,
    ):
        self.value = value
        self.phrase = phrase

    def evaluate(self, text):
        """
        Full-document Boolean evaluation.
        Returns ordinary True / False.
        """

        return term_present(
            self.value,
            text,
            self.phrase,
        )

    def evaluate_partial(self, text):
        """
        Partial evidence evaluation.

        Presence can be established.
        Absence cannot.
        """

        if term_present(
            self.value,
            text,
            self.phrase,
        ):
            return Truth.TRUE

        return Truth.UNKNOWN

    def describe(self):
        if self.phrase:
            return f'"{self.value}"'

        return self.value


class NotNode:
    def __init__(self, child):
        self.child = child

    def evaluate(self, text):
        return not self.child.evaluate(text)

    def evaluate_partial(self, text):
        return truth_not(
            self.child.evaluate_partial(text)
        )

    def describe(self):
        return (
            f"NOT ({self.child.describe()})"
        )


class AndNode:
    def __init__(
        self,
        left,
        right,
    ):
        self.left = left
        self.right = right

    def evaluate(self, text):
        return (
            self.left.evaluate(text)
            and
            self.right.evaluate(text)
        )

    def evaluate_partial(self, text):
        return truth_and(
            self.left.evaluate_partial(text),
            self.right.evaluate_partial(text),
        )

    def describe(self):
        return (
            f"({self.left.describe()} "
            f"AND {self.right.describe()})"
        )


class OrNode:
    def __init__(
        self,
        left,
        right,
    ):
        self.left = left
        self.right = right

    def evaluate(self, text):
        return (
            self.left.evaluate(text)
            or
            self.right.evaluate(text)
        )

    def evaluate_partial(self, text):
        return truth_or(
            self.left.evaluate_partial(text),
            self.right.evaluate_partial(text),
        )

    def describe(self):
        return (
            f"({self.left.describe()} "
            f"OR {self.right.describe()})"
        )


# ============================================================
# PARSER
# ============================================================

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.position = 0

    def current(self):
        return self.tokens[
            self.position
        ]

    def advance(self):
        token = self.current()

        if token[0] != TOKEN_EOF:
            self.position += 1

        return token

    def parse(self):
        expression = self.parse_or()

        if self.current()[0] != TOKEN_EOF:
            raise SyntaxError(
                "Unexpected token: "
                f"{self.current()[1]}"
            )

        return expression

    def parse_or(self):
        node = self.parse_and()

        while (
            self.current()[0]
            == TOKEN_OR
        ):
            self.advance()

            node = OrNode(
                node,
                self.parse_and(),
            )

        return node

    def parse_and(self):
        node = self.parse_not()

        while True:
            token_type = (
                self.current()[0]
            )

            if token_type == TOKEN_AND:
                self.advance()

                node = AndNode(
                    node,
                    self.parse_not(),
                )

                continue

            # Support:
            #
            # A NOT B
            #
            # as shorthand for:
            #
            # A AND NOT B

            if token_type == TOKEN_NOT:
                right = self.parse_not()

                node = AndNode(
                    node,
                    right,
                )

                continue

            # Implicit AND:
            #
            # DJD Dividend ETF
            #
            # means:
            #
            # DJD AND Dividend AND ETF
            #
            # Adjacency is deterministic syntax, not semantic expansion.
            if token_type in {
                TOKEN_TERM,
                TOKEN_PHRASE,
                TOKEN_LPAREN,
            }:
                node = AndNode(
                    node,
                    self.parse_not(),
                )

                continue

            break

        return node

    def parse_not(self):
        if (
            self.current()[0]
            == TOKEN_NOT
        ):
            self.advance()

            return NotNode(
                self.parse_not()
            )

        return self.parse_primary()

    def parse_primary(self):
        token_type, value = (
            self.current()
        )

        if token_type == TOKEN_TERM:
            self.advance()

            return TermNode(
                value,
                phrase=False,
            )

        if token_type == TOKEN_PHRASE:
            self.advance()

            return TermNode(
                value,
                phrase=True,
            )

        if token_type == TOKEN_LPAREN:
            self.advance()

            expression = (
                self.parse_or()
            )

            if (
                self.current()[0]
                != TOKEN_RPAREN
            ):
                raise SyntaxError(
                    "Missing closing parenthesis."
                )

            self.advance()

            return expression

        raise SyntaxError(
            "Expected a search term, "
            "phrase, or '('. "
            f"Found: {value}"
        )


def parse_query(query):
    return Parser(
        tokenize(query)
    ).parse()


# ============================================================
# RETRIEVAL PROJECTION
# ============================================================

def _positive_projection(node):
    """
    Create a positive-only approximation
    for candidate retrieval.

    Returns:
        query_string_or_None,
        recall_safe
    """

    if isinstance(node, TermNode):
        return (
            node.describe(),
            True,
        )

    if isinstance(node, NotNode):
        return None, True

    if isinstance(node, AndNode):
        (
            left_query,
            left_safe,
        ) = _positive_projection(
            node.left
        )

        (
            right_query,
            right_safe,
        ) = _positive_projection(
            node.right
        )

        safe = (
            left_safe
            and right_safe
        )

        if (
            left_query
            and right_query
        ):
            return (
                f"({left_query} "
                f"AND {right_query})",
                safe,
            )

        if left_query:
            return (
                left_query,
                safe,
            )

        if right_query:
            return (
                right_query,
                safe,
            )

        return None, safe

    if isinstance(node, OrNode):
        (
            left_query,
            left_safe,
        ) = _positive_projection(
            node.left
        )

        (
            right_query,
            right_safe,
        ) = _positive_projection(
            node.right
        )

        if (
            left_query
            and right_query
        ):
            return (
                f"({left_query} "
                f"OR {right_query})",
                (
                    left_safe
                    and right_safe
                ),
            )

        # Example:
        #
        # A OR NOT B
        #
        # Searching only A could miss
        # pages satisfying NOT B.

        if (
            left_query
            or right_query
        ):
            return (
                left_query
                or right_query,
                False,
            )

        return None, False

    raise TypeError(
        f"Unknown node type: "
        f"{type(node)}"
    )


def build_retrieval_query(
    expression
):
    query, recall_safe = (
        _positive_projection(
            expression
        )
    )

    if not query:
        return None, False

    return query, recall_safe


# ============================================================
# EXPLANATION HELPERS
# ============================================================

def context_around(
    text,
    needle,
    radius=110,
):
    normalized_needle = (
        normalize_text(needle)
    )

    position = text.find(
        normalized_needle
    )

    if position == -1:
        return None

    start = max(
        0,
        position - radius,
    )

    end = min(
        len(text),
        position
        + len(normalized_needle)
        + radius,
    )

    snippet = text[
        start:end
    ].strip()

    if start > 0:
        snippet = "..." + snippet

    if end < len(text):
        snippet += "..."

    return snippet


def explanation_lines(
    node,
    text,
    indent=2,
):
    """
    Explanation for a complete live document.
    """

    pad = " " * indent
    result = node.evaluate(text)

    if isinstance(node, TermNode):
        display = node.describe()

        if result:
            return [
                f"{pad}✓ {display} present"
            ]

        return [
            f"{pad}✗ {display} absent"
        ]

    if isinstance(node, NotNode):
        lines = []

        child_result = (
            node.child.evaluate(text)
        )

        if result:
            lines.append(
                f"{pad}✓ NOT satisfied"
            )
        else:
            lines.append(
                f"{pad}✗ NOT failed"
            )

        child = node.child

        if isinstance(
            child,
            TermNode,
        ):
            display = (
                child.describe()
            )

            if child_result:
                lines.append(
                    f"{pad}  ✗ "
                    f"{display} present"
                )

                snippet = (
                    context_around(
                        text,
                        child.value,
                    )
                )

                if snippet:
                    lines.append(
                        f"{pad}    "
                        f"{snippet}"
                    )

            else:
                lines.append(
                    f"{pad}  ✓ "
                    f"{display} absent"
                )

        else:
            lines.extend(
                explanation_lines(
                    child,
                    text,
                    indent + 2,
                )
            )

        return lines

    if isinstance(node, AndNode):
        if result:
            lines = [
                f"{pad}✓ AND satisfied"
            ]
        else:
            lines = [
                f"{pad}✗ AND failed"
            ]

        lines.extend(
            explanation_lines(
                node.left,
                text,
                indent + 2,
            )
        )

        lines.extend(
            explanation_lines(
                node.right,
                text,
                indent + 2,
            )
        )

        return lines

    if isinstance(node, OrNode):
        if result:
            lines = [
                f"{pad}✓ OR satisfied"
            ]
        else:
            lines = [
                f"{pad}✗ OR failed"
            ]

        lines.extend(
            explanation_lines(
                node.left,
                text,
                indent + 2,
            )
        )

        lines.extend(
            explanation_lines(
                node.right,
                text,
                indent + 2,
            )
        )

        return lines

    return []


# ============================================================
# PARTIAL-EVIDENCE EXPLANATION
# ============================================================

def partial_explanation_lines(
    node,
    text,
    indent=2,
):
    """
    Explanation for incomplete/indexed evidence.

    A term not appearing in the evidence
    is UNKNOWN, never FALSE.
    """

    pad = " " * indent

    result = (
        node.evaluate_partial(text)
    )

    if isinstance(node, TermNode):
        display = node.describe()

        if result == Truth.TRUE:
            return [
                f"{pad}✓ {display} "
                "present in indexed evidence"
            ]

        return [
            f"{pad}? {display} "
            "not established"
        ]

    if isinstance(node, NotNode):
        lines = []

        if result == Truth.TRUE:
            lines.append(
                f"{pad}✓ NOT established"
            )

        elif result == Truth.FALSE:
            lines.append(
                f"{pad}✗ NOT disproved"
            )

        else:
            lines.append(
                f"{pad}? NOT unresolved"
            )

        lines.extend(
            partial_explanation_lines(
                node.child,
                text,
                indent + 2,
            )
        )

        return lines

    if isinstance(node, AndNode):
        if result == Truth.TRUE:
            symbol = "✓"
            message = "AND established"

        elif result == Truth.FALSE:
            symbol = "✗"
            message = "AND disproved"

        else:
            symbol = "?"
            message = "AND unresolved"

        lines = [
            f"{pad}{symbol} {message}"
        ]

        lines.extend(
            partial_explanation_lines(
                node.left,
                text,
                indent + 2,
            )
        )

        lines.extend(
            partial_explanation_lines(
                node.right,
                text,
                indent + 2,
            )
        )

        return lines

    if isinstance(node, OrNode):
        if result == Truth.TRUE:
            symbol = "✓"
            message = "OR established"

        elif result == Truth.FALSE:
            symbol = "✗"
            message = "OR disproved"

        else:
            symbol = "?"
            message = "OR unresolved"

        lines = [
            f"{pad}{symbol} {message}"
        ]

        lines.extend(
            partial_explanation_lines(
                node.left,
                text,
                indent + 2,
            )
        )

        lines.extend(
            partial_explanation_lines(
                node.right,
                text,
                indent + 2,
            )
        )

        return lines

    return []