# Contributing

Thank you for contributing to Cadence! This guide covers our conventions for commits, branches, and pull requests.

## Commit Message Format

We use [Conventional Commits](https://www.conventionalcommits.org/) for clear, automated changelog generation.

### Format

```
<type>(<scope>): <subject>

[optional body]

[optional footer(s)]
```

### Types

| Type       | Description                     | Example                                  |
| ---------- | ------------------------------- | ---------------------------------------- |
| `feat`     | New feature                     | `feat(nl2sql): add template matching`    |
| `fix`      | Bug fix                         | `fix(api): handle null response`         |
| `docs`     | Documentation only              | `docs: update README setup`              |
| `style`    | Formatting, no code change      | `style: fix indentation`                 |
| `refactor` | Code change, no new feature/fix | `refactor(workflow): extract validators` |
| `perf`     | Performance improvement         | `perf(search): add result caching`       |
| `test`     | Adding/updating tests           | `test(extractor): add param tests`       |
| `build`    | Build system, dependencies      | `build: update to Python 3.13`           |
| `ci`       | CI/CD changes                   | `ci: add GitHub Actions workflow`        |
| `chore`    | Maintenance, tooling            | `chore: update prek hooks`               |
| `revert`   | Revert previous commit          | `revert: revert feat(auth)`              |

### Scope (Optional)

- `api` - API endpoints
- `orchestrator` - ConversationOrchestrator
- `nl2sql` - NL2SQL Controller
- `extractor` - ParameterExtractor
- `validator` - ParameterValidator / QueryValidator
- `builder` - QueryBuilder
- `workflow` - MAF workflow
- `frontend` - Next.js frontend
- `infra` - Infrastructure, IaC
- `config` - Configuration
- `deps` - Dependencies

### Subject Rules

- Use **imperative mood**: "add" not "added" or "adds"
- **Don't capitalize** the first letter
- **No period** at the end
- Keep under **50 characters**

## Branch Naming

Two naming styles are accepted; either one passes the pre-commit branch policy check.

**Manual branches** (typed with ticket):

```
<type>/<ticket>-<short-description>
docs/<short-description>            # docs-only shortcut, no ticket required
```

Examples: `feat/123-template-matching`, `fix/456-null-response`, `docs/readme-update`

**Spec Kit branches** (created by `/speckit.specify` or the bundled scripts):

```
NNN-<short-description>             # sequential, e.g. 007-foundry-agent-threads
YYYYMMDD-HHMMSS-<short-description> # timestamp, e.g. 20260319-143022-foundry-agent-threads
```

Do not rename Spec Kit branches to add a `<type>/` prefix — the spec directory under `specs/` mirrors the branch name and downstream slash commands depend on it.

## Pull Requests

### PR Title

Follow the same Conventional Commits format:

```
feat(nl2sql): add template matching via AI Search
```

### PR Checklist

Before submitting:

- [ ] Code follows project [coding standards](CODING_STANDARD.md)
- [ ] Tests pass locally (`uv run poe check`)
- [ ] Documentation updated if needed
- [ ] Commit messages follow Conventional Commits
- [ ] PR title follows Conventional Commits

## Code Review

### For Authors

- Keep PRs focused and small (< 400 lines ideal)
- Respond to feedback constructively

### For Reviewers

- Be constructive and specific
- Approve if good enough, don't block on nitpicks

## Git Hooks

This project uses git hooks for automated validation:

| Hook         | Purpose                                |
| ------------ | -------------------------------------- |
| `commit-msg` | Validates Conventional Commits format  |
| `pre-commit` | Runs linting and formatting (via prek) |
| `pre-push`   | Runs tests                             |

Hooks are installed automatically by `./devsetup.sh`.

To skip hooks temporarily (not recommended):

```bash
git commit --no-verify -m "message"
```

## See Also

- [DEV_SETUP.md](DEV_SETUP.md) - Development environment setup
- [CODING_STANDARD.md](CODING_STANDARD.md) - Code style and conventions
- [AGENTS.md](AGENTS.md) - AI agent quick reference
