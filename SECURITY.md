# Security

Do not report API keys or other credentials in public issues.

Strictly Boolean expects the Brave Search API key in the `BRAVE_API_KEY` environment variable. Keys should never be committed to the repository or embedded in client-side code.

For a public deployment, place the Flask application behind a production WSGI server/reverse proxy and add rate limiting, caching, and hard API-budget controls before exposing it broadly.
