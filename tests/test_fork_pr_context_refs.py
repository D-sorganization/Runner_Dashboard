"""Quote-aware detection of untrusted GitHub Actions context references."""

from collections.abc import Callable

import pytest

from scripts.fork_pr_context_refs import reads_head_context
from scripts.fork_pr_guard_analysis import reads_head

pytestmark = pytest.mark.unit
DETECTORS = [
    pytest.param(reads_head_context, id="context-scanner"),
    pytest.param(reads_head, id="analysis-helper"),
]


@pytest.mark.parametrize(
    "text",
    [
        "${{ toJSON(github.event.pull_request.head) }}",
        "${{ github.event.pull_request.head }}",
        "${{ toJSON(github.event.pull_request) }}",
        "${{ toJSON(github.event.workflow_run) }}",
        "${{ toJSON(github.event) }}",
        "${{ toJSON(github) }}",
        "${{ github.event.pull_request.head.repo.owner.login }}",
        "${{ github.head_ref }}",
        "${{ github.event.before }}",
        "${{ github.event.after }}",
        "${{ GITHUB . EVENT . AFTER }}",
        "${{ github['event']['before'] }}",
        "${{ github.event.workflow_run.head_sha }}",
        "${{ github.event.workflow_run.head_branch }}",
        "${{ github.event.workflow_run.head_repository.default_branch }}",
        "${{ github.event.workflow_run.head_repository_id }}",
        "${{ github.event.workflow_run.head_commit }}",
        "${{ github.event.workflow_run.head_commit.sha }}",
        "${{ github.event.pull_request.merge_commit_sha }}",
        "${{ github.event.pull_request.diff_url }}",
        "${{ github.event.pull_request.patch_url }}",
        "${{ github.event.workflow_run.path }}",
        "${{ github['event']['workflow_run']['path'] }}",
        "${{ github.event.issue.pull_request.diff_url }}",
        "${{ github.event.issue.pull_request.patch_url }}",
        "${{ toJSON(github.event.issue.pull_request) }}",
        "${{ toJSON(github.event.issue) }}",
        "${{ github.event.issue[inputs.key] }}",
        "${{ github.event[inputs.payload] }}",
        "${{ github.event.pull_request[inputs.payload] }}",
        "${{ github.event.workflow_run[inputs.payload] }}",
        "${{ github[inputs.root] }}",
        "${{ github.* }}",
        "${{ github.event.* }}",
        "${{ github.event.pull_request.* }}",
        "${{ github.event.workflow_run.* }}",
        "${{ GITHUB . EVENT . PULL_REQUEST . HEAD . REPO . NAME }}",
        "${{ github [ 'event' ] [ 'pull_request' ] [ 'head' ] [ 'repo' ] [ 'name' ] }}",
        "${{ format('}} {0}', github.event.pull_request.head.sha) }}",
        "${{ format('it''s }} {0}', github.event.pull_request.head.sha) }}",
        ("${{ github.repository }}-${{ toJSON(github.event.pull_request.head) }}"),
    ],
)
@pytest.mark.parametrize("detect", DETECTORS)
def test_head_roots_and_opaque_ancestors_are_detected(detect: Callable[[str], bool], text: str) -> None:
    assert detect(text)


@pytest.mark.parametrize(
    "text",
    [
        "${{ toJSON(github.event.workflow_run.pull_requests) }}",
        "${{ github.event.workflow_run.pull_requests[0].head.sha }}",
        "${{ github.event.workflow_run.pull_requests[0].head.repo.name }}",
        "${{ github.event.workflow_run.pull_requests[0] }}",
        "${{ github.event.workflow_run.pull_requests.* }}",
        "${{ github.event.workflow_run.pull_requests.*.head.sha }}",
        "${{ github.event.workflow_run.pull_requests.*.head.repo.name }}",
        "${{ toJSON(github.event.workflow_run.referenced_workflows) }}",
        "${{ github.event.workflow_run.referenced_workflows[0].path }}",
        "${{ github.event.workflow_run.referenced_workflows[0].sha }}",
        "${{ github.event.workflow_run.referenced_workflows.*.ref }}",
        "${{ github.event.workflow_run.referenced_workflows[0] }}",
    ],
)
@pytest.mark.parametrize("detect", DETECTORS)
def test_workflow_run_pull_request_collection_contains_untrusted_heads(
    detect: Callable[[str], bool], text: str
) -> None:
    assert detect(text)


@pytest.mark.parametrize(
    "text",
    [
        "'github.event.pull_request'",
        '"github.event.pull_request.head.sha"',
        "${{ format('{0}', 'github.event.pull_request') }}",
        "${{ format('{0}', 'github.event.pull_request.head.sha') }}",
        "${{ toJSON(inputs.github) }}",
        "${{ env.github }}",
        "const api = github; await api.rest.issues.listForRepo({});",
        "${{ github.event.pull_request.number }}",
        "${{ github.event.pull_request.base }}",
        "${{ github.event.pull_request.base.repo.name }}",
        "${{ github.event.workflow_run.conclusion }}",
        "${{ github.event.workflow_run.pull_requests[0].number }}",
        "${{ github.event.workflow_run.pull_requests[0].base.sha }}",
        "${{ github.event.workflow_run.pull_requests.*.number }}",
        "${{ github.event.workflow_run.pull_requests.*.base.sha }}",
        "${{ github.event.repository.default_branch }}",
        "${{ github.repository }}",
        "${{ github.event.pull_request.headline.repo }}",
        "${{ github.event.pull_request.head_extra.repo }}",
        "${{ github.event.workflow_run.head_repository_extra }}",
        "${{ github.event.workflow_run.head_commitment }}",
        "${{ github.event.pull_request.base.repository }}",
        "${{ github.event.afterward }}",
        "${{ github.event.before_update }}",
        "${{ github.event.issue.number }}",
        "${{ github.event.issue.body }}",
        "${{ github.event.issue.pull_request.url }}",
        "${{ github.event.issue.pull_request.html_url }}",
        "${{ github.event.issue.pull_request.merged_at }}",
        "${{ github.event.stack.base.sha }}",
        "${{ github.event.workflow.path }}",
        "${{ github.event.workflow_run.workflow_url }}",
        "${{ github.event.workflow_run.workflow_id }}",
        "${{ 'github.event.after' }}",
        "${{ format('{0}', 'github.event.before') }}",
        "const pr = github.event.pull_request; const head = pr.head;",
    ],
)
@pytest.mark.parametrize("detect", DETECTORS)
def test_literals_other_roots_and_concrete_metadata_are_allowed(detect: Callable[[str], bool], text: str) -> None:
    assert not detect(text)


@pytest.mark.parametrize("detect", DETECTORS)
def test_github_script_client_outside_interpolation_is_allowed(
    detect: Callable[[str], bool],
) -> None:
    script = (
        "const api = github; await api.rest.issues.listForRepo({owner: context.repo.owner, repo: context.repo.repo});"
    )

    assert not detect(script)


@pytest.mark.parametrize("detect", DETECTORS)
def test_github_script_with_real_parent_interpolation_is_flagged(
    detect: Callable[[str], bool],
) -> None:
    script = 'const payload = "${{ toJSON(github.event.pull_request) }}";'

    assert detect(script)


def test_quoted_alias_name_is_not_a_head_reference() -> None:
    assert not reads_head("${{ 'env.PAYLOAD' }}", aliases=("PAYLOAD",))


@pytest.mark.parametrize("detect", DETECTORS)
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("${{ github.event.pull_request.head.sha", True),
        ("${{ 'github.event.pull_request.head.sha'", False),
        ("${{ " * 4096 + "github.event.pull_request.head.sha", True),
    ],
)
def test_unclosed_expression_is_scanned_deterministically(
    detect: Callable[[str], bool], text: str, expected: bool
) -> None:
    assert detect(text) is expected
