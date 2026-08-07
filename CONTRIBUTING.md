# Contributing to kalshi_bot

Thank you for your interest in contributing! Contributions are what make the open-source community a great place to learn and grow.

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/DavidEscotoDev/kalshi_bot/issues) first.
2. Open a bug report with a clear title and description.
3. Include reproduction steps, expected vs. actual behavior, and your environment.

### Suggesting Features

1. Check existing feature requests first.
2. Open a feature request explaining the use case and expected behavior.

### Pull Requests

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/amazing-feature`).
3. Make your changes.
4. Run tests and linting (see below).
5. Commit with a conventional message (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
6. Push and open a Pull Request.

## Development Setup

```bash
git clone https://github.com/DavidEscotoDev/kalshi_bot.git
cd kalshi_bot
pip install -e ".[dev]"
```

## Code Style

- Linted with [ruff](https://github.com/astral-sh/ruff).
- Format: `ruff format .`
- Type-check: `mypy .`
- Tests: `pytest`

## Testing

Run the full suite before opening a PR:

```bash
ruff check .
pytest tests/ -v
```

## License

By contributing, you agree that your contributions will be licensed under the project's MIT License.