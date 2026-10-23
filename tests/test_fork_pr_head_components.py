"""Head repository components remain untrusted in privileged runner jobs."""

import pytest

from scripts import fork_pr_runner_guard as guard

pytestmark = pytest.mark.unit

TARGET_ONLY = "github.event_name == 'pull_request_target'"


def _job_violations(
    context: str,
    step: dict[str, object] | None = None,
    *,
    job_fields: dict[str, object] | None = None,
    job_env: dict[str, str] | None = None,
    workflow_env: dict[str, str] | None = None,
) -> list[str]:
    """Check a step or reusable call in the requested privileged context."""
    job: dict[str, object] = {} if job_fields and "uses" in job_fields else {"runs-on": "d-sorg-fleet"}
    if step is not None:
        job["steps"] = [step]
    if job_env is not None:
        job["env"] = job_env
    if job_fields is not None:
        job.update(job_fields)
    if context == "workflow_call":
        job["if"] = TARGET_ONLY
        events = {"workflow_call"}
    else:
        events = {context}
    workflow = {"env": workflow_env} if workflow_env is not None else None
    return guard.job_violations("components.yml", events, "metadata", job, workflow)


OWNER = "github.event.pull_request.head.repo.owner.login"
REPO = "github.event.pull_request.head.repo.name"
FORMATTED_REPO = "${{ format('{0}/{1}', " + OWNER + ", " + REPO + ") }}"
BASE_REPO = (
    "${{ format('{0}/{1}', "
    "github.event.pull_request.base.repo.owner.login, "
    "github.event.pull_request.base.repo.name) }}"
)


@pytest.mark.parametrize("context", ["workflow_call", "pull_request_target"])
@pytest.mark.parametrize(
    "step",
    [
        pytest.param(
            {
                "run": (
                    f'git clone "https://github.com/${{{{ {OWNER} }}}}/${{{{ {REPO} }}}}.git" repo && bash repo/ci.sh'
                )
            },
            id="component-built-clone-url-and-execution",
        ),
        pytest.param(
            {
                "uses": "actions/checkout@v4",
                "with": {"repository": FORMATTED_REPO},
            },
            id="checkout-formatted-repository",
        ),
        pytest.param(
            {
                "uses": "./.github/actions/record-metadata",
                "with": {"payload": FORMATTED_REPO},
            },
            id="nested-action-input",
        ),
        pytest.param(
            {
                "env": {
                    "HEAD_OWNER": f"${{{{ {OWNER} }}}}",
                    "HEAD_REPO": f"${{{{ {REPO} }}}}",
                },
                "run": ('git clone "https://github.com/$HEAD_OWNER/$HEAD_REPO.git" repo && bash repo/ci.sh'),
            },
            id="step-env-aliases-handed-to-command",
        ),
        pytest.param(
            {
                "run": (
                    'git clone "https://${{ '
                    "github.event.pull_request.head['repo']['owner']['login']"
                    ' }}/${{ github.event.pull_request.head["repo"]["name"] }}.git"'
                )
            },
            id="bracket-property-access",
        ),
        pytest.param(
            {
                "run": (
                    'git clone "https://${{ '
                    "GITHUB.EVENT.PULL_REQUEST.HEAD.REPO.OWNER.LOGIN"
                    ' }}/${{ github.event.pull_request.head.repo.name }}.git"'
                )
            },
            id="case-insensitive-properties",
        ),
        pytest.param(
            {
                "uses": "./.github/actions/record-metadata",
                "with": {"payload": "${{ toJSON(github.event.pull_request.head.repo) }}"},
            },
            id="whole-head-repository-object",
        ),
    ],
)
def test_head_repository_components_are_rejected_in_privileged_jobs(context: str, step: dict[str, object]) -> None:
    assert len(_job_violations(context, step)) == 1


def test_workflow_run_head_repository_components_are_rejected() -> None:
    step = {
        "run": (
            'git clone "https://${{ '
            "github.event.workflow_run.head_repository.owner.login"
            ' }}/${{ github.event.workflow_run.head_repository.name }}.git" repo'
            " && bash repo/ci.sh"
        )
    }

    assert len(_job_violations("workflow_run", step)) == 1


@pytest.mark.parametrize("context", ["workflow_call", "pull_request_target"])
def test_nested_reusable_workflow_call_receiving_head_repository_is_rejected(
    context: str,
) -> None:
    job = {
        "uses": "./.github/workflows/consume-repository.yml",
        "with": {"repository": FORMATTED_REPO},
    }

    assert len(_job_violations(context, job_fields=job)) == 1


def test_nested_reusable_call_cannot_receive_head_repository_default_branch() -> None:
    job = {
        "uses": "./.github/workflows/consume-repository.yml",
        "with": {"ref": "${{ github.event.workflow_run.head_repository.default_branch }}"},
    }

    assert len(_job_violations("workflow_run", job_fields=job)) == 1


@pytest.mark.parametrize("context", ["workflow_call", "pull_request_target"])
@pytest.mark.parametrize(
    "payload",
    [
        "${{ toJSON(github.event.pull_request.head) }}",
        "${{ github.event.pull_request.head }}",
        "${{ toJSON(github.event.pull_request) }}",
        "${{ toJSON(github.event.workflow_run) }}",
        "${{ toJSON(github.event) }}",
        "${{ toJSON(github) }}",
        "${{ github.event[inputs.payload] }}",
        "${{ github.event.before }}",
        "${{ github.event.after }}",
        "${{ github.event.issue.pull_request.diff_url }}",
        "${{ github.event.issue.pull_request.patch_url }}",
    ],
)
def test_opaque_or_dynamic_event_payload_inputs_are_rejected(context: str, payload: str) -> None:
    step = {
        "uses": "./.github/actions/consume-payload",
        "with": {"payload": payload},
    }

    assert len(_job_violations(context, step)) == 1


def test_head_payload_alias_handed_to_command_is_rejected() -> None:
    step = {
        "env": {"HEAD_PAYLOAD": "${{ toJSON(github.event.pull_request.head) }}"},
        "run": 'python consume_payload.py "$HEAD_PAYLOAD"',
    }

    assert len(_job_violations("pull_request_target", step)) == 1


@pytest.mark.parametrize(
    "payload",
    [
        "${{ toJSON(github.event.pull_request.head) }}",
        "${{ github.event.before }}",
        "${{ github.event.after }}",
    ],
)
def test_nested_reusable_call_cannot_receive_serialized_pr_head(payload: str) -> None:
    job = {
        "uses": "./.github/workflows/consume-payload.yml",
        "with": {"payload": payload},
    }

    assert len(_job_violations("workflow_call", job_fields=job)) == 1


@pytest.mark.parametrize(
    "payload",
    [
        "${{ toJSON(github.event.workflow_run.pull_requests) }}",
        "${{ github.event.workflow_run.pull_requests[0].head.sha }}",
        "${{ github.event.workflow_run.pull_requests[0].head.repo.name }}",
        "${{ toJSON(github.event.workflow_run.referenced_workflows) }}",
        "${{ github.event.workflow_run.referenced_workflows[0].path }}",
        "${{ github.event.workflow_run.referenced_workflows[0].sha }}",
        "${{ github.event.workflow_run.referenced_workflows.*.ref }}",
        "${{ github.event.workflow_run.path }}",
    ],
)
def test_workflow_run_code_paths_are_rejected_from_step_inputs(
    payload: str,
) -> None:
    step = {
        "uses": "./.github/actions/consume-payload",
        "with": {"payload": payload},
    }

    assert len(_job_violations("workflow_run", step)) == 1


def test_nested_reusable_call_cannot_receive_workflow_run_pull_request_head() -> None:
    job = {
        "uses": "./.github/workflows/consume-pull-request.yml",
        "with": {"ref": "${{ github.event.workflow_run.pull_requests[0].head.sha }}"},
    }

    assert len(_job_violations("workflow_run", job_fields=job)) == 1


@pytest.mark.parametrize(
    "reference",
    [
        "${{ ENV.PAYLOAD }}",
        "${{ env.payload }}",
        "${{ env . PAYLOAD }}",
        "${{ env [ 'PAYLOAD' ] }}",
        "${{ toJSON(env) }}",
        "${{ env[inputs.key] }}",
    ],
)
def test_workflow_env_head_payload_alias_is_rejected_in_actions_expression(
    reference: str,
) -> None:
    step = {
        "uses": "./.github/actions/consume-payload",
        "with": {"payload": reference},
    }
    workflow_env = {"PAYLOAD": "${{ toJSON(github.event.pull_request) }}"}

    assert len(_job_violations("pull_request_target", step, workflow_env=workflow_env)) == 1


def test_lowercase_posix_variable_does_not_match_uppercase_env_alias() -> None:
    step = {"run": 'printf "%s" "$payload"'}
    workflow_env = {"PAYLOAD": "${{ toJSON(github.event.pull_request) }}"}

    assert _job_violations("pull_request_target", step, workflow_env=workflow_env) == []


@pytest.mark.parametrize("reference", ["$ENV:PAYLOAD", "$env:payload", "${ENV:PAYLOAD}"])
def test_powershell_env_alias_for_head_payload_is_rejected(reference: str) -> None:
    step = {"shell": "pwsh", "run": f"python consume_payload.py {reference}"}
    workflow_env = {"PAYLOAD": "${{ toJSON(github.event.pull_request) }}"}

    assert len(_job_violations("pull_request_target", step, workflow_env=workflow_env)) == 1


def test_workflow_run_head_commit_object_is_rejected() -> None:
    step = {
        "uses": "./.github/actions/consume-payload",
        "with": {"commit": "${{ toJSON(github.event.workflow_run.head_commit) }}"},
    }

    assert len(_job_violations("workflow_run", step)) == 1


def test_github_script_client_without_context_interpolation_is_allowed() -> None:
    step = {
        "uses": "actions/github-script@v7",
        "with": {
            "script": (
                "const api = github; "
                "await api.rest.issues.listForRepo({owner: context.repo.owner, "
                "repo: context.repo.repo});"
            )
        },
    }

    assert _job_violations("pull_request_target", step) == []


@pytest.mark.parametrize(
    ("context", "expression"),
    [
        ("pull_request_target", "${{ github.event.pull_request.number }}"),
        ("workflow_run", "${{ github.event.workflow_run.conclusion }}"),
        ("workflow_run", "${{ github.event.workflow_run.pull_requests[0].number }}"),
        ("workflow_run", "${{ github.event.workflow_run.pull_requests[0].base.sha }}"),
        ("workflow_run", "${{ github.event.workflow_run.pull_requests.*.number }}"),
        ("pull_request_target", "${{ github.repository }}"),
    ],
)
def test_non_head_event_metadata_is_allowed(context: str, expression: str) -> None:
    step = {"run": f'echo "{expression}"'}

    assert _job_violations(context, step) == []


@pytest.mark.parametrize("context", ["workflow_call", "pull_request_target"])
def test_trusted_base_metadata_is_allowed(context: str) -> None:
    step = {
        "uses": "actions/checkout@v4",
        "with": {
            "ref": "${{ github.sha }}",
            "repository": BASE_REPO,
        },
    }

    assert _job_violations(context, step) == []


@pytest.mark.parametrize("context", ["workflow_call", "pull_request_target"])
def test_head_components_in_step_name_are_ignored(context: str) -> None:
    step = {
        "name": f"${{{{ {OWNER} }}}}",
        "run": "echo trusted metadata",
    }

    assert _job_violations(context, step) == []


@pytest.mark.parametrize("context", ["workflow_call", "pull_request_target"])
def test_unreferenced_job_env_alias_is_ignored(context: str) -> None:
    env = {"HEAD_REPO": f"${{{{ {REPO} }}}}"}
    step = {"run": "echo trusted metadata"}

    assert _job_violations(context, step, job_env=env) == []


@pytest.mark.parametrize("context", ["workflow_call", "pull_request_target"])
@pytest.mark.parametrize(
    "reference",
    [
        "github.event.pull_request.headline.repo",
        "github.event.pull_request.head_extra.repo",
        "github.event.workflow_run.head_repository_extra",
    ],
)
def test_near_miss_head_roots_are_allowed(context: str, reference: str) -> None:
    step = {"run": f'echo "${{{{ {reference} }}}}"'}

    assert _job_violations(context, step) == []
