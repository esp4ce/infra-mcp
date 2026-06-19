# Contributing

## Setup

```bash
git clone https://github.com/your-org/infra-mcp
cd infra-mcp
uv pip install -e ".[dev]"
```

## Tests

```bash
pytest
```

No live VM or database required — tests use mocks and pure functions.

## Lint

```bash
ruff check .
```
