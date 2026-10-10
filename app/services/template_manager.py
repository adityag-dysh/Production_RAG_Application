from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

_env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[2] / "prompts"),
                   undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)


def render(name: str, **ctx) -> str:
    return _env.get_template(name).render(**ctx)
