"""Language-agnostic structural chunking: split at top-level definitions, pack to size.
Upgrade path: tree-sitter for true AST chunking (see README)."""
import re
from app.services.parsing import Chunk

MAX_CHARS = 2500
_DEF = re.compile(r"^(async\s+def |def |class |function |export |public |private |protected |func |fn |impl |"
                  r"interface |type |const \w+\s*=\s*\(|@)")


def parse(filename: str, data: bytes) -> list[Chunk]:
    lines = data.decode("utf-8", errors="replace").splitlines()
    blocks, cur = [], []
    for ln in lines:
        if _DEF.match(ln) and cur and not cur[-1].startswith("@"):
            blocks.append(cur); cur = []
        cur.append(ln)
    if cur:
        blocks.append(cur)
    chunks, buf, size, start = [], [], 0, 1
    line_no = 1

    def emit(end_line):
        nonlocal buf, size
        if buf:
            chunks.append(Chunk(f"# file: {filename}\n" + "\n".join(buf), f"{filename}:{start}-{end_line}",
                                {"path": filename}))
        buf, size = [], 0

    for b in blocks:
        text_len = sum(len(x) + 1 for x in b)
        if buf and size + text_len > MAX_CHARS:
            emit(line_no - 1); start = line_no
        if text_len > MAX_CHARS:  # giant function: split by lines
            sub = []
            for ln in b:
                sub.append(ln)
                if sum(len(x) + 1 for x in sub) > MAX_CHARS:
                    buf += sub; emit(line_no + len(sub) - 1); line_no += len(sub); start = line_no; sub = []
            buf += sub; size += sum(len(x) + 1 for x in sub); line_no += len(sub)
        else:
            buf += b; size += text_len; line_no += len(b)
    emit(line_no - 1)
    return chunks
