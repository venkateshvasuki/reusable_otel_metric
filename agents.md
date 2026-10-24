# Agent Guidelines

1. Always enter plan mode before making any change that is requested.
2. Always open pull requests as drafts.
3. Trim comments aggressively; keep only what is necessary.
4. Adhere to strong functional programming principles when writing code.
5. Never use `# type: ignore` or `Any`; solve type gaps with real type definitions (protocols, aliases), and rely on ruff `ANN401` to catch `Any`.