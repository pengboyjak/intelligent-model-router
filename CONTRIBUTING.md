# Contributing to Intelligent Model Router

Thank you for considering contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/pengboyjak/intelligent-model-router.git
cd intelligent-model-router
pip install -r requirements.txt
pip install pytest pytest-asyncio
python gateway.py --port 8701 --reload
```

## Contribution Workflow

1. **Fork** the repository
2. **Create a branch**: `git checkout -b feature/your-feature-name`
3. **Make changes**: follow the code style and add tests where applicable
4. **Run tests**: `pytest`
5. **Commit**: use clear commit messages (`feat:`, `fix:`, `docs:`, `refactor:`)
6. **Push**: `git push origin feature/your-feature-name`
7. **Open a Pull Request** against the `master` branch

## Code Style

- Python: Follow PEP 8. Use type hints.
- TypeScript: Use the existing patterns in `router.ts`.
- HTML/CSS: BEM-like naming, CSS custom properties for theming.
- All UI text must support i18n via `data-i18n` attributes.

## Adding a New Provider

1. Add the provider definition to `_get_all_providers()` in `gateway.py`
2. If a new API format, add the adapter class in `providers.py`
3. Add the provider to `config.yaml` under `providers:`
4. Add i18n keys to all 5 language objects in `static/index.html`

## Adding a New Language

1. Add a new locale object `'xx': {...}` to the `LOCALES` constant in `static/index.html`
2. Translate all existing keys
3. Add the language option to the lang-switcher `<select>` in `init()`

## Pull Request Guidelines

- One feature/fix per PR
- Include a clear description of what and why
- Reference related issues
- Ensure the gateway starts without errors
- Test the Web UI loads correctly

## Reporting Issues

Use [GitHub Issues](https://github.com/pengboyjak/intelligent-model-router/issues) and include:

- Environment (OS, Python version)
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs or screenshots

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
