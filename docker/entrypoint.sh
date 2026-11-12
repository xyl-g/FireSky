#!/bin/sh
set -eu

# Persist one private deployment password without exposing it in logs or argv.
if [ -z "${FIRESKY_HTTP_TOKEN:-}" ] && [ -z "${FIRESKY_HTTP_TOKEN_FILE:-}" ]; then
    token_file=/data/state/http-token
    mkdir -p /data/state
    if [ ! -e "$token_file" ] && [ ! -L "$token_file" ]; then
        umask 077
        temporary="$(mktemp /data/state/.http-token.XXXXXX)"
        trap 'rm -f "$temporary"' EXIT HUP INT TERM
        token="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
        case "$token" in
            *[!0-9a-f]*|'') echo "cannot generate HTTP token" >&2; exit 1 ;;
        esac
        [ "${#token}" -eq 64 ] || { echo "cannot generate HTTP token" >&2; exit 1; }
        printf '%s\n' "$token" > "$temporary"
        # A concurrent startup must never replace an already established secret.
        ln "$temporary" "$token_file" 2>/dev/null || [ -f "$token_file" ]
        rm -f "$temporary"
        trap - EXIT HUP INT TERM
    fi
    export FIRESKY_HTTP_TOKEN_FILE="$token_file"
fi

exec "$@"
