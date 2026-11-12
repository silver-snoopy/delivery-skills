# Where agents look for skills and instructions

Checked on 2026-11-12 against the vendors' own documentation. `scripts/install-skills.sh` uses the locations marked "used".

## Claude Code

Source: "Extend Claude with skills", https://code.claude.com/docs/en/skills

| Scope | Location | Status |
|---|---|---|
| Project | `.claude/skills/<name>/SKILL.md` (loaded from the start directory and each parent up to the repository root) | verified, used |
| Personal | `~/.claude/skills/<name>/SKILL.md` | verified, used (`--user`) |

## GitHub Copilot in VS Code

Source: "Use Agent Skills in VS Code", https://code.visualstudio.com/docs/copilot/customization/agent-skills

| Scope | Location | Status |
|---|---|---|
| Project | `.github/skills/<name>/SKILL.md` | verified, used |
| Personal | `~/.copilot/skills/<name>/SKILL.md` | verified, used (`--user`) |
| `.claude/skills/`, `.agents/skills/` | Listed for the Claude and Codex harnesses and for (deprecated) local sessions, not for the Copilot harness. The page says its table is not every directory recognised. | not relied on |

## GitHub Copilot CLI

Source: "Adding agent skills for GitHub Copilot CLI", https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills (seen in a docs.github.com search summary only; the page itself was not fetched)

| Scope | Location | Status |
|---|---|---|
| Project | `.github/skills/`, `.claude/skills/` or `.agents/skills/` | unverified (seen in a search summary only; the page was not fetched); `.github/skills` used |
| Personal | `~/.copilot/skills/` (also `~/.agents/skills/`) | unverified (seen in a search summary only; the page was not fetched); `~/.copilot/skills` used |

## Repository instructions (AGENTS.md)

Source: "Support for different types of custom instructions", https://docs.github.com/en/copilot/reference/custom-instructions-support

- VS Code Copilot Chat reads `.github/copilot-instructions.md` and an `AGENTS.md` file: verified.
- Copilot CLI reads `.github/copilot-instructions.md` and `AGENTS.md` (also `CLAUDE.md`, `GEMINI.md`): verified.
- Where `AGENTS.md` must be placed: not stated on that page; the repository root is assumed. Unverified.
- This repository has `AGENTS.md` at its root and no `.github/copilot-instructions.md`.

## Not verified

- That each agent actually finds the installed skills and can run the launcher on the customer machine (Windows with WSL, VS Code opened in WSL). This needs a manual check there: open each agent, ask it to list skills, and run the setup skill's first step.
- Whether VS Code Copilot also reads `.claude/skills/` in the current (non-local) harness.
