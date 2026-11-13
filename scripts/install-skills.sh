#!/usr/bin/env bash
# Install the Delivery Ledger skills where agents look for them.
#
#   scripts/install-skills.sh [--project DIR | --user] [--agents claude,copilot] [--force]
#
# --project DIR  install into DIR/.claude/skills and DIR/.github/skills
#                (default: this repository)
# --user         install into ~/.claude/skills and ~/.copilot/skills
# --agents LIST  comma-separated: claude, copilot (default: both)
# --force        overwrite installed files whose content differs
#
set -eu

usage() {
    echo "usage: $0 [--project DIR | --user] [--agents claude,copilot] [--force]" >&2
    exit 2
}

die() {
    echo "error: $1" >&2
    exit 1
}

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$SCRIPT_DIR/.." && pwd)
LAUNCHER="$REPO/bin/ledger"
[ -x "$LAUNCHER" ] || die "launcher not found at $LAUNCHER"

MODE=project
TARGET="$REPO"
AGENTS="claude,copilot"
FORCE=0
SEEN_SCOPE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --project)
            [ $# -ge 2 ] || usage
            [ "$SEEN_SCOPE" -eq 0 ] || die "use only one of --project and --user"
            SEEN_SCOPE=1; MODE=project; TARGET=$2; shift 2 ;;
        --user)
            [ "$SEEN_SCOPE" -eq 0 ] || die "use only one of --project and --user"
            SEEN_SCOPE=1; MODE=user; shift ;;
        --agents)
            [ $# -ge 2 ] || usage
            AGENTS=$2; shift 2 ;;
        --force)
            FORCE=1; shift ;;
        -h|--help)
            usage ;;
        *)
            echo "error: unknown option $1" >&2; usage ;;
    esac
done

if [ "$MODE" = project ]; then
    [ -d "$TARGET" ] || die "project directory $TARGET does not exist"
    TARGET=$(cd "$TARGET" && pwd)
fi

DESTS=""
OLD_IFS=$IFS
IFS=,
for agent in $AGENTS; do
    case "$agent" in
        claude)
            if [ "$MODE" = user ]; then d="$HOME/.claude/skills"; else d="$TARGET/.claude/skills"; fi ;;
        copilot)
            if [ "$MODE" = user ]; then d="$HOME/.copilot/skills"; else d="$TARGET/.github/skills"; fi ;;
        *)
            IFS=$OLD_IFS; die "unknown agent '$agent' (use claude, copilot)" ;;
    esac
    DESTS="$DESTS$d
"
done
IFS=$OLD_IFS
[ -n "$DESTS" ] || die "no agents given"

# The launcher path as it goes inside double quotes in the shell line
# LEDGER="...": escape backslash, double quote, dollar and backtick.
QUOTED=$(printf '%s' "$LAUNCHER" | sed -e 's/[\\"$`]/\\&/g')
# Then escape it again for use as a sed replacement with | as delimiter.
SED_PATH=$(printf '%s' "$QUOTED" | sed -e 's/[\\&|]/\\&/g')

render() {
    # $1 source file; prints the installed content. Only the LEDGER= line
    # is substituted.
    sed -e "/^[[:space:]]*LEDGER=\"__LEDGER_PATH__\"[[:space:]]*\$/s|__LEDGER_PATH__|$SED_PATH|" "$1"
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Pass 1: render every target into WORK and check for conflicts.
# Nothing is written until every target has been checked.
PLAN="$WORK/plan"
: > "$PLAN"
CONFLICTS=0
COUNT=0
NL='
'
IFS=$NL
for dest in $DESTS; do
    for skill_dir in "$REPO"/skills/*/; do
        [ -d "$skill_dir" ] || continue
        name=$(basename "$skill_dir")
        for rel in $(cd "$skill_dir" && find . -type f | sort); do
            rel=${rel#./}
            target="$dest/$name/$rel"
            COUNT=$((COUNT + 1))
            rendered="$WORK/$COUNT"
            render "$skill_dir$rel" > "$rendered" || die "could not read $skill_dir$rel"
            if [ -e "$target" ]; then
                if cmp -s "$rendered" "$target"; then
                    echo "unchanged $target"
                    continue
                fi
                if [ "$FORCE" -ne 1 ]; then
                    echo "error: $target exists and differs" >&2
                    CONFLICTS=$((CONFLICTS + 1))
                    continue
                fi
            fi
            printf '%s\n%s\n' "$rendered" "$target" >> "$PLAN"
        done
    done
done
IFS=$OLD_IFS

if [ "$CONFLICTS" -gt 0 ]; then
    echo "error: $CONFLICTS file(s) differ; nothing was written. Rerun with --force to overwrite them." >&2
    exit 1
fi

# Pass 2: write.
while IFS= read -r rendered && IFS= read -r target; do
    mkdir -p "$(dirname "$target")" || die "could not create $(dirname "$target")"
    cp "$rendered" "$target" || die "could not write $target"
    echo "wrote $target"
done < "$PLAN"
