# Contributing — branch rules

`main` is **owner-only**: only the repository owner (`@luxopes`) pushes to or
merges into `main`. This applies to everyone else, regardless of access level.

For everyone else:

1. **Never commit directly to `main`.** Work in your own branch.
2. **How to get a branch:**
   * with write access: push a feature branch to this repository, or
   * without write access (default): fork the repository and push the branch to
     your fork (`allow_forking` is enabled).
3. **Open a pull request** from your branch against `main`. One approval from
   the owner is required before merge; the owner merges PRs.
4. Never force-push or delete `main`.

Branch name suggestion: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`.

> Note: on the current private Free plan GitHub does not enforce these rules
> server-side, so they rely on this agreement. Enforcement (ruleset) is applied
> once the repository moves to a plan that supports it.
