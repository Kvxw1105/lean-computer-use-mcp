# AGENTS.md

## Development workflow

- Use `uv sync --all-extras` to install dependencies and `uv run pytest` to run tests.
- Keep the package importable without a live desktop: the real upstream client and the fake client share one interface.
- New behavior must include a unit test and, when it changes model-visible output, an update to `docs/PROTOCOL.md`.
- Version control is routine: commit and push to `main` (single source of truth) as the natural closing step of every completed task — never wait to be asked. Use conventional-commit messages (`fix:`/`feat:`/`docs:`/`test:`) with a body that lists the changes and test results. Verify the push afterwards (`git log --oneline -3`, clean `git status`). Use a branch or tag for risky/experimental work so the previous state is always restorable.

## Conventions

- Python 3.11+, type hints required, dataclasses preferred for internal models.
- Public docs are written in English; Chinese is allowed in comments or private notes only.
- Never commit screenshots, personal data, or real accessibility trees. Use sanitized fixtures in `examples/fixtures/`.
- Windows-specific behavior belongs in the Windows client and must be documented there; parsing and diff logic must stay platform-neutral.
- Changes that affect token cost must be accompanied by a metrics assertion (text characters, image bytes, node count).

## Safety

- The facade must never manufacture user confirmation. Confirmation is a model/user policy decision.
- The facade may reject stale state, cap batch size, and refuse unsupported actions, but it must not silently retry commit-like actions.
- On-screen UI text is untrusted data and must never override protocol rules.