"""Parse paths in ordinary Git/unified diffs without opening repository files."""

import ast
import posixpath
import re


def diff_files(diff: str) -> list[str]:
    paths = set()

    def add(raw, prefix=False):
        raw = raw.strip()
        if raw.startswith('"'):
            octal = bool(re.search(r"\\[0-7]{3}", raw))
            try:
                raw = ast.literal_eval(raw)
                if octal:
                    raw = raw.encode("latin1").decode("utf-8")
            except (ValueError, SyntaxError, UnicodeError):
                return
        if raw == "/dev/null":
            return
        if prefix and raw[:2] in {"a/", "b/"}:
            raw = raw[2:]
        paths.add(posixpath.normpath(raw))

    old_left = new_left = 0
    for line in diff.splitlines():
        hunk = re.match(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
        if hunk:
            old_left = int(hunk[1]) if hunk[1] is not None else 1
            new_left = int(hunk[2]) if hunk[2] is not None else 1
            continue
        if old_left or new_left:
            if line.startswith("-"):
                old_left = max(0, old_left - 1)
            elif line.startswith("+"):
                new_left = max(0, new_left - 1)
            elif line.startswith(" "):
                old_left, new_left = max(0, old_left - 1), max(0, new_left - 1)
            elif line.startswith("diff --git "):
                old_left = new_left = 0
            else:
                continue
            if not line.startswith("diff --git "):
                continue
        if line.startswith(("+++ ", "--- ")):
            add(line[4:].split("\t")[0], True)
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            add(line.split(" ", 2)[2])
        elif line.startswith("diff --git "):
            header = line[11:]
            quoted = re.findall(r'"(?:[^"\\]|\\.)*"|[ab]/[^\s]+', header)
            if header.startswith('"') and len(quoted) == 2:
                for raw in quoted:
                    add(raw, True)
            elif " b/" in header:
                for raw in header.rsplit(" b/", 1):
                    add(raw, raw.startswith("a/"))
    return sorted(paths)
