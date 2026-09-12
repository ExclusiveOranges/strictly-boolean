# Contributing to Strictly Boolean

Strictly Boolean values deterministic behavior, transparent evidence, and reproducible tests.

When proposing a change, please keep these principles in mind:

1. **Do not silently reinterpret the user's query.** Syntax rules may be deterministic; semantic guessing should not alter the user's Boolean expression.
2. **Keep retrieval separate from verification.** An upstream search provider may supply candidate URLs, but the local verifier determines Boolean truth.
3. **Do not infer absence from incomplete evidence.** Partial indexed evidence can establish presence, but missing snippet text is not proof that a term is absent from the document.
4. **Ranking must not alter qualification.** A page either satisfies the expression, fails it, or cannot be established from available evidence before ranking is considered.
5. **Prefer explicit uncertainty.** When evidence is incomplete or conflicting, return an unverifiable state rather than manufacturing certainty.
6. **Add regression tests for behavioral changes.** Useful tests include parser precedence, nested expressions, phrase handling, `NOT`, site constraints, license constraints, blocked pages, thin pages, and indexed-evidence fallback.

Please do not include API keys, credentials, private datasets, or personal local paths in issues or pull requests.
