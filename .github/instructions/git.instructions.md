---
applyTo: "**/COMMIT_EDITMSG, **/.git/**, **/.githooks/**"
---

# Git Commit Guidelines

When helping users write commit messages, follow the Conventional Commits format defined in [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Quick Reference

### Format

```
<type>(<scope>): <subject>
```

### Types

- `feat` - New feature
- `fix` - Bug fix
- `docs` - Documentation only
- `style` - Formatting, no code change
- `refactor` - Code change, no new feature/fix
- `perf` - Performance improvement
- `test` - Adding/updating tests
- `build` - Build system, dependencies
- `ci` - CI/CD changes
- `chore` - Maintenance, tooling
- `revert` - Revert previous commit

### Common Scopes

- `api` - API endpoints
- `auth` - Authentication/authorization
- `db` - Database, models, migrations
- `core` - Core business logic
- `cli` - Command-line interface
- `config` - Configuration
- `deps` - Dependencies
- `infra` - Infrastructure, IaC

### Rules

1. Use imperative mood: "add" not "added" or "adds"
2. Don't capitalize first letter of subject
3. No period at end
4. Keep subject under 50 characters (max 72)
5. Use `!` for breaking changes. Example: `feat(api)!: change response format`

### Examples

```
feat(auth): add OAuth2 login flow
fix(api): handle null response gracefully
docs: update installation guide
refactor(db): extract query builder class
test(auth): add login validation tests
chore(deps): update ruff to v0.15
feat(api)!: migrate to JSON:API format
```

## Branch Names

When suggesting branch names:

```
feat/oauth-login
feat/123-oauth-login
fix/456-null-response
docs/readme-update
```

When creating or renaming branches for the user, enforce branch policy proactively:

1. Preferred pattern for manual branches: `<type>/<short-description>`
2. Manual branches may also include a ticket prefix: `<type>/<ticket>-<short-description>`
3. Allowed docs-only shortcut: `docs/<short-description>`
4. Spec Kit branches use their native naming and must not be reprefixed:
   - Sequential: `NNN-<short-description>` (e.g. `007-foundry-agent-threads`)
   - Timestamp: `YYYYMMDD-HHMMSS-<short-description>`
5. Do not invent ticket IDs; only include one when provided by the user
