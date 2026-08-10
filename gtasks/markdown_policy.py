"""Pure Markdown contract for newly created Mission Control Tasks and Tickets."""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Iterable, Mapping
from html import unescape
from urllib.parse import quote, unquote, urlsplit


class MarkdownContractError(ValueError):
    """Raised when generated Markdown cannot meet the canonical contract."""


MARKDOWN_CONTRACT = "unified-task-ticket-v1"


@dataclass(frozen=True, slots=True)
class SystemTicketReference:
    slug: str
    title: str


TASK_SLUG_RE = re.compile(
    r"(?<![A-Za-z0-9_/%?#.-])(tasks/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"(?![A-Za-z0-9_/%?#-]|\.[A-Za-z0-9])",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
MARKDOWN_REFERENCE_DEFINITION_RE = re.compile(
    r"^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*(?:<([^>\s]+)>|([^\s]+))",
    re.MULTILINE,
)
MARKDOWN_AUTOLINK_RE = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]*:[^<>\s]*)>")
HTML_URL_ATTRIBUTE_RE = re.compile(
    r"\b(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))",
    re.IGNORECASE,
)
INTERNAL_TICKET_ROUTE_RE = re.compile(
    r"#system-ticket/(tasks%2F[0-9a-f-]{36})"
    r"(?![A-Za-z0-9_/%?#.-])",
    re.IGNORECASE,
)


def extract_system_ticket_slugs(value: str) -> tuple[str, ...]:
    """Return canonical Task-shaped slugs once, in their source order."""
    visible = _mask_markdown_code(value)
    found = [(match.start(), match.group(1)) for match in TASK_SLUG_RE.finditer(visible)]
    for match in INTERNAL_TICKET_ROUTE_RE.finditer(visible):
        encoded = match.group(1)
        slug = unquote(encoded)
        if system_ticket_route(slug) == "#system-ticket/" + encoded:
            found.append((match.start(), slug))
    found.sort(key=lambda item: item[0])
    result: list[str] = []
    seen: set[str] = set()
    for _position, slug in found:
        key = slug.casefold()
        if key not in seen:
            seen.add(key)
            result.append(slug)
    return tuple(result)


def _extract_plain_task_slugs(value: str) -> tuple[str, ...]:
    visible = _mask_markdown_code(value)
    return tuple(
        dict.fromkeys(match.group(1) for match in TASK_SLUG_RE.finditer(visible))
    )


def reference_is_explicitly_labeled_system_ticket(
    slug: str, values: Iterable[str]
) -> bool:
    marker = re.compile(
        rf"(?:System Ticket|系统工单)\s*[:：#-]?\s*{re.escape(slug)}",
        re.IGNORECASE,
    )
    return any(marker.search(value) for value in values)


def system_ticket_route(slug: str) -> str:
    if not TASK_SLUG_RE.fullmatch(slug):
        raise MarkdownContractError("System Ticket slug is not canonical")
    return "#system-ticket/" + quote(slug, safe="")


def validate_generated_markdown(
    value: str,
    verified_references: Mapping[str, SystemTicketReference | None] | None = None,
) -> None:
    """Reject generated links that are not safe external or internal routes."""
    visible = _mask_markdown_code(value)
    for label, target in MARKDOWN_LINK_RE.findall(visible):
        _validate_markdown_target(
            target,
            label=label,
            verified_references=verified_references,
        )
    for angled_target, bare_target in MARKDOWN_REFERENCE_DEFINITION_RE.findall(visible):
        _validate_markdown_target(angled_target or bare_target)
    for target in MARKDOWN_AUTOLINK_RE.findall(visible):
        _validate_markdown_target(target)
    for quoted_double, quoted_single, unquoted in HTML_URL_ATTRIBUTE_RE.findall(visible):
        _validate_markdown_target(unescape(quoted_double or quoted_single or unquoted))


def _validate_markdown_target(
    target: str,
    *,
    label: str | None = None,
    verified_references: Mapping[str, SystemTicketReference | None] | None = None,
) -> None:
    if target.startswith("#system-ticket/"):
        encoded = target.removeprefix("#system-ticket/")
        slug = unquote(encoded)
        if system_ticket_route(slug) != target:
            raise MarkdownContractError("unsafe internal Ticket route")
        if verified_references is not None:
            reference = verified_references.get(slug)
            if (
                reference is None
                or reference.slug != slug
                or label != _escape_link_label(reference.title)
            ):
                raise MarkdownContractError(
                    "internal route is not an exact verified canonical System Ticket"
                )
        return
    parsed = urlsplit(target)
    if not (
        parsed.scheme == "https"
        or (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
        )
    ):
        raise MarkdownContractError("unsafe generated Markdown link")


def render_task_body(
    title: str,
    detail: str,
    references: Mapping[str, SystemTicketReference | None],
) -> str:
    """Project canonical Task fields without changing the authored detail."""
    body = f"# {_escape_heading(title)}\n\n## 详情\n\n{detail}"
    references_section = _render_references((detail,), references)
    if references_section:
        body += "\n\n## 关联的 System Tickets\n\n" + references_section
    validate_generated_markdown(body, references)
    return body


def render_system_ticket_body(
    title: str,
    verbatim_request: str,
    *,
    acceptance_criteria: str = "",
    linked_evidence: Iterable[str] = (),
    implementation_receipts: Iterable[str] = (),
    qa_receipts: Iterable[str] = (),
    references: Mapping[str, SystemTicketReference | None] | None = None,
) -> str:
    """Project the canonical System Ticket fields in their approved order."""
    evidence = tuple(linked_evidence)
    implementation = tuple(implementation_receipts)
    qa = tuple(qa_receipts)
    references = {} if references is None else references
    body = f"# {_escape_heading(title)}\n\n## 用户请求\n\n{verbatim_request}"
    if acceptance_criteria:
        body += f"\n\n## 验收标准\n\n{acceptance_criteria}"
    references_section = _render_references(
        (verbatim_request, acceptance_criteria, *evidence, *implementation, *qa),
        references,
    )
    if references_section:
        body += "\n\n## 关联票据\n\n" + references_section
    receipts = (*evidence, *implementation, *qa)
    if receipts:
        body += "\n\n## 实施与验证记录\n\n" + "\n".join(
            f"- {receipt}" for receipt in receipts
        )
    validate_generated_markdown(body, references)
    return body


def _escape_heading(value: str) -> str:
    """Keep an arbitrary title on the one generated H1 line."""
    flattened = _flatten_heading(value)
    return re.sub(r"([\\`*{}\[\]()<>#+\-.!_|])", r"\\\1", flattened)


def _flatten_heading(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).strip()


def _escape_link_label(value: str) -> str:
    """Keep canonical titles inside the generated Markdown link label."""
    return (
        _flatten_heading(value)
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "&#93;")
    )


def _render_references(
    values: Iterable[str], references: Mapping[str, SystemTicketReference | None]
) -> str:
    values = tuple(values)
    lines: list[str] = []
    seen: set[str] = set()
    for slug in _extract_plain_task_slugs("\n".join(values)):
        deduplication_key = slug.casefold()
        if deduplication_key in seen or slug not in references:
            continue
        seen.add(deduplication_key)
        reference = references[slug]
        if reference is None:
            if reference_is_explicitly_labeled_system_ticket(slug, values):
                lines.append(f"- System Ticket unavailable: {slug}")
        else:
            if reference.slug != slug:
                raise MarkdownContractError("System Ticket reference slug does not match")
            lines.append(
                f"- [{_escape_link_label(reference.title)}]({system_ticket_route(slug)})"
            )
    rendered = "\n".join(lines)
    validate_generated_markdown(rendered, references)
    return rendered


def _mask_markdown_code(value: str) -> str:
    """Preserve positions/newlines while removing non-rendered code literals."""
    masked = list(value)
    offset = 0
    fence_character: str | None = None
    fence_length = 0
    for line in value.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        mask_line = fence_character is not None
        if fence_character is None:
            opening = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", content)
            if opening:
                mask_line = True
                fence_character = opening.group(1)[0]
                fence_length = len(opening.group(1))
        else:
            closing = re.match(
                rf"^[ \t]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$",
                content,
            )
            if closing:
                fence_character = None
                fence_length = 0
        if mask_line:
            for index in range(offset, offset + len(content)):
                masked[index] = " "
        offset += len(line)

    visible = "".join(masked)
    inline = re.compile(r"(?<!`)(`+)(?!`)([^\n]*?)(?<!`)\1(?!`)")
    masked = list(visible)
    for match in inline.finditer(visible):
        for index in range(match.start(), match.end()):
            if masked[index] not in "\r\n":
                masked[index] = " "
    return "".join(masked)
