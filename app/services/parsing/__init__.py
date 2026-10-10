from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    location: str = ""
    meta: dict = field(default_factory=dict)


CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".cs", ".rb",
            ".php", ".kt", ".swift", ".sql", ".sh", ".yaml", ".yml", ".json", ".toml"}
LOG_EXT = {".log", ".jsonl", ".out"}


def parse(filename: str, data: bytes, doc_type: str) -> list[Chunk]:
    from pathlib import Path
    from app.services.parsing import code_parser, doc_parser, log_parser
    ext = Path(filename).suffix.lower()
    if doc_type == "code" or ext in CODE_EXT:
        return code_parser.parse(filename, data)
    if doc_type == "log" or ext in LOG_EXT:
        return log_parser.parse(filename, data)
    return doc_parser.parse(filename, data)
