"""Standard-library source identity shared by runtime and build tooling."""
import os
from pathlib import Path
import re
import stat
import subprocess


class SourceIdentityError(ValueError):
    pass


def source_paths(root):
    root = Path(root)
    paths = [root / name for name in ("pyproject.toml", "setup.py", "MANIFEST.in", "build_identity.py",
                                    "README.md", "LICENSE", "NOTICE")]
    for folder, pattern in (("src/delivery_review", "*.py"), ("schemas", "*.json"),
                            ("formats", "*.json"), ("locks", "*.json")):
        paths.extend((root / folder).rglob(pattern))
    return sorted(path for path in paths if "__pycache__" not in path.parts)


def source_commit(root, paths):
    """Require exact HEAD blob bytes, even when index flags hide a modification.

    No clean/smudge or line-ending conversion is applied. A CRLF working file
    differing from its LF HEAD blob is unverified, even if Git calls it clean.
    """
    root = Path(root).resolve()
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                       GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    def git(*args, data=None):
        return subprocess.check_output(["git", "-c", "core.fsmonitor=false", *args], cwd=root, env=environment,
                                       input=data, stderr=subprocess.DEVNULL, timeout=10)
    def need(condition):
        if not condition:
            raise SourceIdentityError("collector_source_must_be_clean")
    try:
        need(os.path.lexists(root / ".git"))
        commit = git("rev-parse", "HEAD").decode("ascii").strip()
        top = Path(git("rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
        need(top == root and re.fullmatch(r"[0-9a-f]{40}", commit) is not None)
        need(not git("status", "--porcelain", "--untracked-files=normal").strip())
        need(0 < len(paths) <= 4096)
        contents, total = {}, 0
        for path in paths:
            name = path.relative_to(root).as_posix()
            info = path.lstat()
            need(stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400)
            need(info.st_size <= 4 * 1024 * 1024)
            with path.open("rb") as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
            total += len(raw)
            need(len(raw) == info.st_size and total <= 16 * 1024 * 1024)
            contents[name] = raw
        tree = git("ls-tree", "-r", "-z", "--full-tree", commit)
        need(len(tree) <= 16 * 1024 * 1024)
        tracked = {}
        for row in tree.split(b"\0"):
            if row:
                identity, name = row.split(b"\t", 1)
                mode, kind, oid = identity.split(b" ")
                path = name.decode("utf-8")
                if path in contents:
                    need(mode in {b"100644", b"100755"} and kind == b"blob"
                         and re.fullmatch(b"[0-9a-f]{40}", oid) is not None)
                    tracked[path] = oid
        need(set(tracked) == set(contents))
        names = sorted(contents)
        request = b"".join(tracked[name] + b"\n" for name in names)
        checked = git("cat-file", "--batch-check", data=request).splitlines()
        need(len(checked) == len(names))
        sizes = []
        for name, row in zip(names, checked):
            oid, kind, size = row.split(b" ")
            need(oid == tracked[name] and kind == b"blob" and size.isdigit())
            sizes.append(int(size))
        need(all(size <= 4 * 1024 * 1024 for size in sizes) and sum(sizes) <= 16 * 1024 * 1024)
        # --batch without --filters/--textconv reads original blobs verbatim.
        output, offset = git("cat-file", "--batch", data=request), 0
        for name, size in zip(names, sizes):
            header = tracked[name] + b" blob " + str(size).encode() + b"\n"
            need(output[offset:offset + len(header)] == header)
            offset += len(header)
            need(output[offset:offset + size] == contents[name])
            offset += size
            need(output[offset:offset + 1] == b"\n")
            offset += 1
        need(offset == len(output))
        return commit
    except SourceIdentityError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, TypeError) as exc:
        raise SourceIdentityError("collector_source_unavailable") from exc
