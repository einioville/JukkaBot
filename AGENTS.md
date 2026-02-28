# Repository Guidelines

## Project Structure & Module Organization
This repository is currently a clean scaffold. Keep the layout predictable as code is added:
- `src/`: application or library source code.
- `tests/`: automated tests mirroring `src/` paths.
- `assets/`: static files (images, sample data, fixtures).
- `docs/`: architecture notes, ADRs, and onboarding docs.

Example:
```text
src/features/auth/
tests/features/auth/
assets/images/
```

## Build, Test, and Development Commands
No build tooling is committed yet. When adding tooling, expose standard entry points and keep them stable:
- `npm run dev` (or equivalent): run locally with reload.
- `npm test`: run all tests.
- `npm run lint`: run static checks.
- `npm run build`: produce production artifacts.

If the project uses another stack (e.g., `make`, `pytest`, `dotnet`), document equivalent commands in `README.md` and keep this file aligned.

## Coding Style & Naming Conventions
- Use 4 spaces for indentation unless language conventions require otherwise.
- Prefer descriptive, domain-based names (`user_service`, `order_validator`).
- Use `PascalCase` for classes/types, `camelCase` for functions/variables, and `kebab-case` for file names where idiomatic.
- Enforce formatting/linting with project tools (for example Prettier/ESLint, Black/Ruff, or language-native linters).

## Testing Guidelines
- Place tests under `tests/` with paths matching source modules.
- Name tests clearly by behavior (example: `auth_login_rejects_invalid_password`).
- Add unit tests for new logic and regression tests for bug fixes.
- Target meaningful coverage for changed code; avoid untested feature additions.

## Commit & Pull Request Guidelines
With no existing Git history, adopt a conventional format:
- Commit style: `type(scope): short summary` (e.g., `feat(auth): add token refresh flow`).
- Keep commits focused and atomic.
- PRs should include: purpose, key changes, test evidence, and linked issue/task.
- Include screenshots or logs when UI or behavior changes are visible.

## Security & Configuration Tips
- Never commit secrets; use environment variables and a checked-in `.env.example`.
- Pin dependency versions where practical.
- Review new dependencies for maintenance and license suitability.
