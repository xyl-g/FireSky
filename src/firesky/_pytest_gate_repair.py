"""Pytest plugin repairing gate-runner quoting damage in ``sys.argv``.

The CI gate runner occasionally passes the focused pytest command with the
test paths *and* the trailing ``-k "a or b"`` expression joined into a single
quoted argument (a shell-quoting bug upstream of pytest), e.g.

    python -m pytest 'tests/test_x.py -k "receipt or tampered"' -q

pytest's ``Config.parse`` tokenizes ``argv`` as-is: the joined string lands in
``file_or_dir`` and later fails ``resolve_collection_argument`` with
``ERROR: file or directory not found: -k …`` (exit code 4) — ``pytest_collection``
hooks run *after* parsing, so a conftest cannot fix it (and
``pytest_load_initial_conftests`` is not called for conftest files at all,
see ``_pytest/hookspec.py``).  A ``pytest11`` entry-point plugin, however,
runs during that early hook and may rewrite ``args`` in place before the real
parse happens.

This module must stay dependency-free and tiny: it is imported by every
pytest run of the project.
"""

import os
import shlex

# Value-taking options whose value may have been quoted and joined together
# with paths into one argument.
_VALUE_OPTIONS = ("-k", "-m", "--override-ini")


def _strip_quotes(value: str) -> str:
    """Remove leftover quote characters from a runner-joined option value."""
    return value.replace('"', "").replace("'", "")


def _looks_like_option(token: str) -> bool:
    """True when ``token`` starts a pytest option rather than being an
    expression word or a path."""
    if token in _VALUE_OPTIONS:
        return True
    for opt in _VALUE_OPTIONS:
        if token.startswith(opt) and len(token) > len(opt):
            if token[len(opt)] in ("=", '"', "'"):
                return True
    return token.startswith("--")


def _resolve_path_token(token: str):
    """Existing path for ``token`` (as-is, or relative to tests/), else None."""
    candidate = token
    if not os.path.exists(candidate):
        candidate = os.path.join("tests", token)
        if not os.path.exists(candidate):
            return None
    return candidate


def _split_joined_tokens(parts):
    """Re-tokenize runner-joined pytest args: resolve path tokens and give a
    value option (``-k``/``-m``/``--override-ini``) every following
    non-option token — de-quoted — as its value.  Returns the repaired arg
    list, or None when a non-option token is not an existing path (meaning
    this argument is not quoting damage we know how to fix)."""
    rebuilt = []
    i = 0
    n = len(parts)
    while i < n:
        part = parts[i]
        matched = None
        for opt in _VALUE_OPTIONS:
            if part == opt:
                matched = (opt, "")
                break
            if part.startswith(opt) and len(part) > len(opt):
                rest = part[len(opt):]
                if rest[0] in ("=", '"', "'"):
                    matched = (opt, rest)
                    break
        if matched is not None:
            opt, attached = matched
            value_parts = [attached] if attached else []
            j = i + 1
            while j < n and not _looks_like_option(parts[j]):
                value_parts.append(parts[j])
                j += 1
            rebuilt.append(opt)
            if value_parts:
                rebuilt.append(_strip_quotes(" ".join(value_parts)))
            i = j
            continue
        if part.startswith("--"):
            rebuilt.append(part)
            i += 1
            continue
        resolved = _resolve_path_token(part)
        if resolved is None:
            return None
        rebuilt.append(resolved)
        i += 1
    return rebuilt


def repair_joined_pytest_args(args) -> bool:
    """Repair gate-runner quoting damage in-place.  Returns True when any
    argument was rewritten.

    Two damage shapes are repaired, both only when the joined value does not
    exist as a filesystem path:

    1. several bare test paths joined into one argument
       (``"tests/a.py tests/b.py"``) — split apart;
    2. test paths joined with a value option (``"tests/x.py -k \"a or b\""``
       or ``"tests/x.py -k a or b"``) — re-tokenized so the option survives
       as a real option with the de-quoted expression as its value.
    """
    repaired = []
    changed = False
    for arg in args:
        candidate = None
        if " " in arg and not os.path.exists(arg):
            parts = [part for part in arg.split(" ") if part]
            if parts and all(os.path.exists(part) for part in parts):
                candidate = parts
            else:
                prefixed = [os.path.join("tests", part) for part in parts]
                if parts and all(os.path.exists(part) for part in prefixed):
                    candidate = prefixed
            if candidate is None and any(
                _looks_like_option(part) for part in parts
            ):
                try:
                    tokens = shlex.split(arg, posix=False)
                except ValueError:
                    tokens = None
                if tokens:
                    candidate = _split_joined_tokens(tokens)
        if candidate is None:
            repaired.append(arg)
        else:
            repaired.extend(candidate)
            changed = True
    if changed:
        args[:] = repaired
    return changed


def pytest_load_initial_conftests(early_config, args) -> None:
    """Early hook: runs before ``Config.parse`` consumes ``file_or_dir``,
    which is the only point where joined arguments can still be separated
    into paths and real options."""
    repair_joined_pytest_args(args)
