"""Sandboxed Jinja2 templates with a fixed placeholder catalog (Section 13)."""

import re
from dataclasses import dataclass, field

from jinja2 import StrictUndefined, TemplateSyntaxError, meta
from jinja2.exceptions import UndefinedError
from jinja2.sandbox import ImmutableSandboxedEnvironment, SecurityError

_env = ImmutableSandboxedEnvironment(autoescape=True, undefined=StrictUndefined)

# Anything that could pull in remote content or run code is refused at upload.
FORBIDDEN_MARKUP = [
    (re.compile(r"<\s*script", re.I), "<script> tags are not allowed"),
    (re.compile(r"<\s*(iframe|object|embed)", re.I), "embedded frames/objects are not allowed"),
    (re.compile(r"""(src|href)\s*=\s*["']?\s*(https?:|file:|//)""", re.I),
     "external URLs are not allowed; embed images as data: URIs"),
    (re.compile(r"@import", re.I), "CSS @import is not allowed"),
    (re.compile(r"url\(\s*['\"]?\s*(https?:|file:|//)", re.I), "external CSS url() is not allowed"),
]


class TemplateRenderError(Exception):
    pass


@dataclass
class TemplateCheck:
    placeholders: list[str] = field(default_factory=list)
    unknown_placeholders: list[str] = field(default_factory=list)
    missing_placeholders: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unknown_placeholders or self.missing_placeholders or self.errors)


def check_template(html: str, catalog: set[str], required: set[str]) -> TemplateCheck:
    result = TemplateCheck()
    for pattern, message in FORBIDDEN_MARKUP:
        if pattern.search(html):
            result.errors.append(message)
    try:
        names = meta.find_undeclared_variables(_env.parse(html))
    except TemplateSyntaxError as e:
        result.errors.append(f"Template syntax error on line {e.lineno}: {e.message}")
        return result
    result.placeholders = sorted(names)
    result.unknown_placeholders = sorted(names - catalog)
    result.missing_placeholders = sorted(required - names)
    return result


def render_html(html: str, context: dict) -> str:
    try:
        return _env.from_string(html).render(**context)
    except (UndefinedError, SecurityError, TemplateSyntaxError) as e:
        raise TemplateRenderError(str(e)) from e
