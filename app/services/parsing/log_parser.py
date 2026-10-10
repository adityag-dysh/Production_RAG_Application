"""Logs: fixed line windows with overlap; records time range + level counts so answers can cite time spans."""
import re
from app.services.parsing import Chunk

WINDOW, OVERLAP = 40, 5
_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_LVL = re.compile(r"\b(ERROR|WARN|WARNING|FATAL|CRITICAL)\b")


def parse(filename: str, data: bytes) -> list[Chunk]:
    lines = [l for l in data.decode("utf-8", errors="replace").splitlines() if l.strip()]
    out, step = [], WINDOW - OVERLAP
    for i in range(0, max(len(lines), 1), step):
        win = lines[i:i + WINDOW]
        if not win:
            break
        ts = [m.group(0) for l in win if (m := _TS.search(l))]
        lv: dict[str, int] = {}
        for l in win:
            if m := _LVL.search(l):
                lv[m.group(1)] = lv.get(m.group(1), 0) + 1
        loc = f"{filename} lines {i + 1}-{i + len(win)}" + (f" [{ts[0]} -> {ts[-1]}]" if ts else "")
        header = f"# log window {loc}; levels={lv}\n"
        out.append(Chunk(header + "\n".join(win), loc, {"levels": lv}))
        if i + WINDOW >= len(lines):
            break
    return out
