import unittest

from gtasks.markdown_policy import (
    MarkdownContractError,
    SystemTicketReference,
    extract_system_ticket_slugs,
    render_system_ticket_body,
    render_task_body,
    system_ticket_route,
    validate_generated_markdown,
)


TICKET_SLUG = "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620"
OTHER_TICKET_SLUG = "tasks/0bcdef12-3456-4abc-8def-0123456789ab"


class MarkdownPolicyTests(unittest.TestCase):
    def setUp(self):
        self.refs = {
            TICKET_SLUG: SystemTicketReference(
                slug=TICKET_SLUG,
                title="Implement an event-driven Agent Handoff Dispatcher",
            )
        }

    def test_task_body_preserves_detail_and_adds_verified_ticket_links(self):
        detail = f"Follow {TICKET_SLUG} exactly."
        body = render_task_body("Continue dispatcher", detail, self.refs)
        self.assertIn("# Continue dispatcher\n\n## 详情\n\n" + detail, body)
        self.assertIn(
            "[Implement an event-driven Agent Handoff Dispatcher]"
            "(#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620)",
            body,
        )

    def test_system_ticket_sections_follow_the_approved_order(self):
        body = render_system_ticket_body(
            "Dispatcher ticket",
            f"Keep the original request about {TICKET_SLUG}.",
            acceptance_criteria="The handoff is delivered.",
            linked_evidence=("evidence/a",),
            implementation_receipts=("Implemented worker.",),
            qa_receipts=("QA passed.",),
            references=self.refs,
        )
        self.assertLess(body.index("## 用户请求"), body.index("## 验收标准"))
        self.assertLess(body.index("## 验收标准"), body.index("## 关联票据"))
        self.assertLess(body.index("## 关联票据"), body.index("## 实施与验证记录"))
        self.assertIn("- evidence/a", body)
        self.assertIn("- Implemented worker.", body)
        self.assertIn("- QA passed.", body)

    def test_system_ticket_preserves_exact_user_fields(self):
        request = "Original wording, including  two spaces.\n\n- Do not restyle"
        criteria = "First criterion\nSecond criterion"
        body = render_system_ticket_body(
            "Ticket", request, acceptance_criteria=criteria
        )
        self.assertIn("## 用户请求\n\n" + request, body)
        self.assertIn("## 验收标准\n\n" + criteria, body)

    def test_duplicate_verified_references_render_once(self):
        body = render_task_body(
            "Continue dispatcher", f"{TICKET_SLUG}\nAgain: {TICKET_SLUG}", self.refs
        )
        self.assertEqual(body.count("#system-ticket/"), 1)

    def test_verified_ticket_title_is_escaped_for_a_generated_link(self):
        refs = {
            TICKET_SLUG: SystemTicketReference(
                slug=TICKET_SLUG, title="Plan ](javascript:alert(1)) [draft"
            )
        }
        body = render_task_body("Continue", TICKET_SLUG, refs)
        self.assertIn("[Plan &#93;(javascript:alert(1)) \\[draft]", body)
        self.assertIn("(#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620)", body)

    def test_explicitly_labeled_missing_reference_is_unavailable(self):
        detail = f"System Ticket: {OTHER_TICKET_SLUG}"
        body = render_task_body("Continue", detail, {OTHER_TICKET_SLUG: None})
        self.assertIn("## 关联的 System Tickets", body)
        self.assertIn(f"System Ticket unavailable: {OTHER_TICKET_SLUG}", body)
        self.assertNotIn("#system-ticket/", body)

    def test_unlabeled_missing_reference_is_left_as_authored_text(self):
        detail = f"Follow {OTHER_TICKET_SLUG} when it is ready."
        body = render_task_body("Continue", detail, {OTHER_TICKET_SLUG: None})
        self.assertIn(detail, body)
        self.assertNotIn("## 关联的 System Tickets", body)

    def test_ordinary_task_slug_is_left_unchanged(self):
        detail = f"Follow ordinary task {TICKET_SLUG}."
        body = render_task_body("Continue", detail, {})
        self.assertIn(detail, body)
        self.assertNotIn("## 关联的 System Tickets", body)

    def test_heading_escaping_prevents_extra_headings(self):
        body = render_task_body("First line\n## injected heading", "Detail", {})
        self.assertTrue(body.startswith("# First line \\#\\# injected heading\n"))
        self.assertNotIn("\n## injected heading\n", body)

    def test_system_ticket_route_percent_encodes_canonical_slug(self):
        self.assertEqual(
            system_ticket_route(TICKET_SLUG),
            "#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620",
        )

    def test_extract_system_ticket_slugs_deduplicates_canonical_matches(self):
        self.assertEqual(
            extract_system_ticket_slugs(f"{TICKET_SLUG} then {TICKET_SLUG}"),
            (TICKET_SLUG,),
        )

    def test_extract_system_ticket_slugs_requires_an_exact_token_boundary(self):
        for continuation in (
            "/child",
            "%2Fchild",
            ".json",
            "?view=detail",
            "#fragment",
        ):
            with self.subTest(continuation=continuation):
                self.assertEqual(
                    extract_system_ticket_slugs(TICKET_SLUG + continuation),
                    (),
                )
        self.assertEqual(
            extract_system_ticket_slugs(f"See {TICKET_SLUG}, then continue."),
            (TICKET_SLUG,),
        )
        for prefix in ("/api/", "%2F", "fragment#", "query?"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    extract_system_ticket_slugs(prefix + TICKET_SLUG),
                    (),
                )

    def test_authored_internal_route_requires_exact_verified_title_and_slug(self):
        route = system_ticket_route(TICKET_SLUG)
        canonical = self.refs[TICKET_SLUG].title
        body = render_task_body(
            "Continue",
            f"[{canonical}]({route})",
            self.refs,
        )
        self.assertIn(f"[{canonical}]({route})", body)
        self.assertEqual(body.count(route), 1)
        for detail in (
            f"[Forged title]({route})",
            f"[{canonical}]({system_ticket_route(OTHER_TICKET_SLUG)})",
        ):
            with self.subTest(detail=detail), self.assertRaisesRegex(
                MarkdownContractError, "verified canonical System Ticket"
            ):
                render_task_body("Continue", detail, self.refs)

    def test_extract_system_ticket_slugs_includes_exact_internal_routes(self):
        route = system_ticket_route(TICKET_SLUG)
        self.assertEqual(
            extract_system_ticket_slugs(f"[Dispatcher]({route})"),
            (TICKET_SLUG,),
        )

    def test_authored_internal_route_rejects_unavailable_reference(self):
        with self.assertRaisesRegex(
            MarkdownContractError, "verified canonical System Ticket"
        ):
            render_task_body(
                "Continue",
                f"[Unavailable]({system_ticket_route(OTHER_TICKET_SLUG)})",
                {OTHER_TICKET_SLUG: None},
            )

    def test_validate_generated_markdown_rejects_unsafe_urls(self):
        with self.assertRaisesRegex(MarkdownContractError, "unsafe generated Markdown link"):
            validate_generated_markdown("[bad](javascript:alert(1))")
        with self.assertRaises(MarkdownContractError):
            validate_generated_markdown(
                "[bad](#system-ticket/tasks%252Ffad23bf2-571f-4db0-b9f5-07ab52ae8620)"
            )

    def test_validate_generated_markdown_accepts_safe_links(self):
        validate_generated_markdown(
            "[ticket](#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620) "
            "[external](https://example.com/path) [local](http://localhost:4179/)"
        )

    def test_task_body_rejects_unsafe_authored_markdown_and_preserves_safe_text(self):
        with self.assertRaisesRegex(MarkdownContractError, "unsafe generated Markdown link"):
            render_task_body("Continue", "[unsafe](javascript:alert(1))", {})
        safe_detail = "Source wording:  [safe](https://example.com/path?q=1)\n"
        body = render_task_body("Continue", safe_detail, {})
        self.assertIn(safe_detail, body)

    def test_system_ticket_body_rejects_unsafe_authored_fields(self):
        unsafe_fields = (
            {"verbatim_request": "[unsafe](javascript:alert(1))"},
            {"acceptance_criteria": "[unsafe](data:text/plain,blocked)"},
            {"linked_evidence": ("[unsafe](file:///private/tmp/secret)",)},
            {"implementation_receipts": ("[unsafe](javascript:alert(1))",)},
            {"qa_receipts": ("[unsafe](data:text/plain,blocked)",)},
        )
        for fields in unsafe_fields:
            with self.subTest(fields=fields), self.assertRaises(MarkdownContractError):
                arguments = {"verbatim_request": "Safe request", **fields}
                render_system_ticket_body("Ticket", **arguments)

    def test_public_renderers_reject_unsafe_reference_definitions(self):
        unsafe_targets = (
            "javascript:alert(1)",
            "data:text/plain,blocked",
            "file:///private/tmp/secret",
        )
        for target in unsafe_targets:
            reference_markdown = f"[unsafe][reference]\n\n[reference]: <{target}>"
            with self.subTest(renderer="task", target=target), self.assertRaises(
                MarkdownContractError
            ):
                render_task_body("Continue", reference_markdown, {})
            with self.subTest(renderer="ticket", target=target), self.assertRaises(
                MarkdownContractError
            ):
                render_system_ticket_body("Ticket", reference_markdown)

    def test_reference_definitions_allow_safe_targets(self):
        validate_generated_markdown(
            "[external][e] [local][l] [ticket][t]\n\n"
            "[e]: https://example.com/path\n"
            "[l]: <http://localhost:4179/>\n"
            "[t]: #system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620"
        )

    def test_public_renderers_reject_unsafe_autolinks(self):
        unsafe_targets = (
            "javascript:alert(1)",
            "data:text/plain,blocked",
            "file:///private/tmp/secret",
        )
        for target in unsafe_targets:
            autolink = f"<{target}>"
            with self.subTest(renderer="task", target=target), self.assertRaises(
                MarkdownContractError
            ):
                render_task_body("Continue", autolink, {})
            with self.subTest(renderer="ticket", target=target), self.assertRaises(
                MarkdownContractError
            ):
                render_system_ticket_body("Ticket", autolink)

    def test_public_renderers_allow_safe_autolinks(self):
        autolinks = "<https://example.com/path> <http://127.0.0.1:4179/>"
        self.assertIn(autolinks, render_task_body("Continue", autolinks, {}))
        self.assertIn(autolinks, render_system_ticket_body("Ticket", autolinks))

    def test_public_renderers_reject_unsafe_raw_html_url_attributes(self):
        unsafe_html = (
            '<a HREF="jav&#x61;script:alert(1)">unsafe</a>',
            "<img src='data:text/plain,blocked'>",
            "<iframe SRC=file:///private/tmp/secret></iframe>",
        )
        for html in unsafe_html:
            with self.subTest(renderer="task", html=html), self.assertRaises(
                MarkdownContractError
            ):
                render_task_body("Continue", html, {})
            with self.subTest(renderer="ticket", html=html), self.assertRaises(
                MarkdownContractError
            ):
                render_system_ticket_body("Ticket", html)

    def test_public_renderers_allow_safe_raw_html_url_attributes(self):
        safe_html = (
            '<a href="https://example.com/path">safe</a> '
            "<img src='http://localhost:4179/image.png'> "
            "<iframe src=http://127.0.0.1:4179/frame></iframe>"
        )
        self.assertIn(safe_html, render_task_body("Continue", safe_html, {}))
        self.assertIn(safe_html, render_system_ticket_body("Ticket", safe_html))

    def test_public_renderers_ignore_unsafe_looking_links_inside_code(self):
        code_sample = (
            "Use this literal: `[unsafe](javascript:alert(1))`\n\n"
            "```markdown\n"
            "[unsafe](data:text/plain,blocked)\n"
            "<a href=\"file:///private/tmp/example\">sample</a>\n"
            "```"
        )
        self.assertIn(code_sample, render_task_body("Continue", code_sample, {}))
        self.assertIn(
            code_sample,
            render_system_ticket_body("Ticket", code_sample),
        )

    def test_code_examples_do_not_generate_ticket_references(self):
        detail = f"`{TICKET_SLUG}`\n\n```text\n{TICKET_SLUG}\n```"
        body = render_task_body("Continue", detail, self.refs)
        self.assertNotIn("## 关联的 System Tickets", body)

    def test_generated_heading_escapes_inline_markdown_metacharacters(self):
        body = render_task_body(
            "*bold* [link](https://example.com) `code` # tag",
            "Detail",
            {},
        )
        self.assertTrue(
            body.startswith(
                "# \\*bold\\* \\[link\\]\\(https://example\\.com\\) "
                "\\`code\\` \\# tag\n"
            )
        )


if __name__ == "__main__":
    unittest.main()
