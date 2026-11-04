import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from opensandbox.config import ConnectionConfig

from agent.platform_runtime.artifacts import (
    ArtifactError,
    ReleaseRef,
    load_release,
    verify_manifest,
)
from agent.platform_runtime.sandbox import OpenSandboxProvider, RunScope, SandboxFailure
from agent.platform_runtime.transport import BoundedResponse, SandboxServiceTransport
from agent.platform_runtime.backend import OpenSandboxBackend


IMAGE = "registry.internal/skill@sha256:" + "a" * 64


def package(content=b"---\nname: sample\ndescription: test\n---\nHello"):
    value = {
        "schemaVersion": 1,
        "name": "sample",
        "version": "v1",
        "runtimeImage": IMAGE,
        "dependencyPolicy": "preinstalled-image-only",
        "files": [
            {
                "path": "SKILL.md",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "dependencyLock": False,
            }
        ],
    }
    raw = json.dumps(value, separators=(",", ":")).encode()
    reference = ReleaseRef(uuid4(), hashlib.sha256(raw).hexdigest())
    source = SimpleNamespace(
        manifest=AsyncMock(return_value=raw), file=AsyncMock(return_value=content)
    )
    return value, raw, reference, source


@pytest.mark.asyncio
async def test_exact_manifest_and_file_bytes_are_fixed_even_if_draft_changes():
    _, raw, ref, source = package()
    release = await load_release(source, ref)
    source.file.return_value = b"changed draft"
    assert release.contents[0].startswith(b"---")
    assert verify_manifest(raw, ref.manifest_sha256).runtimeImage == IMAGE
    with pytest.raises(ArtifactError):
        await load_release(source, ref)
    with pytest.raises(ArtifactError):
        verify_manifest(raw + b" ", ref.manifest_sha256)


@pytest.mark.parametrize(
    "path", ["../x", "/etc/passwd", "C:/x", "a\\b", "a//b", "a/./b", "a\n/b", "a./b"]
)
def test_manifest_rejects_paths_before_any_sandbox_write(path):
    value, _, _, _ = package()
    value["files"][0]["path"] = path
    raw = json.dumps(value).encode()
    with pytest.raises(ArtifactError):
        verify_manifest(raw, hashlib.sha256(raw).hexdigest())


def test_duplicate_json_keys_and_mutable_images_are_rejected():
    value, _, _, _ = package()
    value["runtimeImage"] = "registry.internal/skill:latest"
    raw = json.dumps(value).encode()
    with pytest.raises(ArtifactError):
        verify_manifest(raw, hashlib.sha256(raw).hexdigest())
    raw = b'{"schemaVersion":1,"schemaVersion":1}'
    with pytest.raises(ArtifactError):
        verify_manifest(raw, hashlib.sha256(raw).hexdigest())


def scope(environment="test"):
    return RunScope(
        "tenant-a",
        environment,
        uuid4(),
        1,
        uuid4(),
        datetime.now(timezone.utc) + timedelta(minutes=5),
    )


async def provider(profile="kata"):
    _, _, ref, source = package()
    release = await load_release(source, ref)
    sandbox = SimpleNamespace(
        id="sandbox-test",
        files=SimpleNamespace(create_directories=AsyncMock(), write_files=AsyncMock()),
        commands=SimpleNamespace(run=AsyncMock()),
        destroy=AsyncMock(),
    )
    journal = SimpleNamespace(
        reserve=AsyncMock(), creating=AsyncMock(),
        created=AsyncMock(), cleanup_pending=AsyncMock(), released=AsyncMock()
    )
    admission = SimpleNamespace(before_create=AsyncMock(), after_create=AsyncMock())
    factory = AsyncMock(return_value=sandbox)
    config = ConnectionConfig(
        domain="sandbox.internal:8080",
        api_key="synthetic-contract-test-key-000000000",
        use_server_proxy=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    adapter = OpenSandboxProvider(
        config, journal, admission, profile=profile, factory=factory
    )
    return adapter, sandbox, journal, admission, factory, release


@pytest.mark.asyncio
async def test_create_scopes_files_and_credentials_and_destroys_exactly_once():
    adapter, sb, journal, admission, factory, release = await provider()
    run = scope()
    lease = await adapter.create(run, [release])
    kwargs = factory.call_args.kwargs
    assert (
        kwargs["env"] == {} and kwargs["volumes"] == [] and kwargs["extensions"] == {}
    )
    assert kwargs["secure_access"] is True
    assert kwargs["network_policy"].default_action == "deny"
    assert kwargs["metadata"] == run.metadata()
    assert len(kwargs["metadata"]["oa.tenant"]) == 52
    assert all(len(value) <= 63 for value in kwargs["metadata"].values())
    assert kwargs["timeout"].total_seconds() <= 300
    written = sb.files.write_files.call_args.args[0][0]
    assert (
        written.path == "/skills/sample/SKILL.md"
        and written.mode == 444
        and written.owner == "root"
    )
    admission.after_create.assert_awaited_once_with(run, "kata", sb.id)
    await asyncio.gather(lease.close(), lease.close())
    sb.destroy.assert_awaited_once()
    journal.released.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_permission_denies_commands_waiting_for_the_same_sandbox():
    adapter, sb, journal, admission, factory, release = await provider()
    allowed = True

    async def permit():
        if not allowed:
            raise SandboxFailure("LEASE_FENCED")

    adapter.permission = permit
    lease = await adapter.create(scope(), [release])
    entered, unblock = asyncio.Event(), asyncio.Event()

    async def command(*args, **kwargs):
        entered.set()
        await unblock.wait()
        return SimpleNamespace(exit_code=0)

    sb.commands.run.side_effect = command
    first = asyncio.create_task(lease.execute("echo first"))
    await entered.wait()
    second = asyncio.create_task(lease.execute("echo second"))
    await asyncio.sleep(0)
    allowed = False
    unblock.set()
    await first
    with pytest.raises(SandboxFailure, match="LEASE_FENCED"):
        await second
    assert sb.commands.run.await_count == 1
    await lease.close()


@pytest.mark.asyncio
async def test_failed_destroy_is_retried_without_reopening_execution():
    adapter, sb, journal, admission, factory, release = await provider()
    lease = await adapter.create(scope(), [release])
    sb.destroy.side_effect = [RuntimeError("temporary service outage"), None]
    await lease.close()
    journal.released.assert_not_awaited()
    with pytest.raises(SandboxFailure, match="SANDBOX_UNAVAILABLE"):
        await lease.execute("echo cannot-resume")
    await lease.close()
    assert sb.destroy.await_count == 2
    journal.released.assert_awaited_once()


@pytest.mark.asyncio
async def test_journal_outage_after_destroy_retries_recording_without_a_second_delete():
    adapter, sb, journal, admission, factory, release = await provider()
    lease = await adapter.create(scope(), [release])
    journal.released.side_effect = [RuntimeError("temporary database outage"), None]
    await lease.close()
    await lease.close()
    sb.destroy.assert_awaited_once()
    assert journal.released.await_count == 2


@pytest.mark.asyncio
async def test_revocation_during_isolation_admission_prevents_sandbox_creation():
    adapter, sb, journal, admission, factory, release = await provider()
    adapter.permission = AsyncMock(side_effect=[None, SandboxFailure("LEASE_FENCED")])
    with pytest.raises(SandboxFailure, match="LEASE_FENCED"):
        await adapter.create(scope(), [release])
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_production_never_uses_demo_gvisor_or_missing_admission():
    adapter, sb, journal, admission, factory, release = await provider("gvisor-demo")
    with pytest.raises(SandboxFailure, match="ISOLATION_UNAVAILABLE"):
        await adapter.create(scope("prod"), [release])
    factory.assert_not_awaited()
    admission.before_create.side_effect = SandboxFailure("ISOLATION_UNAVAILABLE")
    with pytest.raises(SandboxFailure):
        await adapter.create(scope(), [release])
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_demo_has_unique_execd_auth_instead_of_unsupported_docker_secure_access():
    adapter, sb, journal, admission, factory, release = await provider("gvisor-demo")
    first = await adapter.create(scope(), [release])
    first_args = factory.call_args.kwargs
    await first.close()
    second = await adapter.create(scope(), [release])
    second_args = factory.call_args.kwargs
    token = second_args["env"]["EXECD_ACCESS_TOKEN"]
    assert len(token) >= 48 and token != first_args["env"]["EXECD_ACCESS_TOKEN"]
    assert token != adapter.connection.api_key
    assert second_args["connection_config"].headers["X-EXECD-ACCESS-TOKEN"] == token
    assert "X-EXECD-ACCESS-TOKEN" not in adapter.connection.headers
    assert (
        second_args["secure_access"] is False and second_args["network_policy"] is None
    )
    await second.close()


@pytest.mark.asyncio
async def test_failed_journal_or_post_create_validation_always_kills_sandbox():
    adapter, sb, journal, admission, factory, release = await provider()
    journal.created.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await adapter.create(scope(), [release])
    sb.destroy.assert_awaited_once()
    journal.released.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_is_unprivileged_output_is_bounded_and_client_does_not_accumulate():
    adapter, sb, journal, admission, factory, release = await provider()
    lease = await adapter.create(scope(), [release])

    async def run(command, *, opts, handlers):
        assert opts.uid == 1000 and opts.gid == 1000
        assert opts.working_directory == "/workspace"
        assert handlers.skip_accumulation is True
        assert "ulimit -H -u 128 &&" in command
        assert set(opts.envs) == {"HOME", "TMPDIR", "EXECD_ACCESS_TOKEN"}
        assert opts.envs["EXECD_ACCESS_TOKEN"] == ""
        assert "/usr/bin/env -i PATH=" in command
        assert "ulimit -H -f 65536 &&" in command
        await handlers.on_stdout(SimpleNamespace(text="x" * 100000))
        await handlers.on_stderr(SimpleNamespace(text="must be truncated"))
        return SimpleNamespace(exit_code=0)

    sb.commands.run.side_effect = run
    result = await lease.execute("printf 'test'", timeout=10)
    assert (
        len(result.stdout.encode()) == 64 * 1024
        and result.truncated
        and result.stderr == ""
    )
    await lease.close()


@pytest.mark.asyncio
async def test_line_oriented_execd_events_preserve_text_and_blank_lines():
    adapter, sb, journal, admission, factory, release = await provider()
    lease = await adapter.create(scope(), [release])

    async def run(command, *, opts, handlers):
        for line in ["first", "\n", "second"]:
            await handlers.on_stdout(SimpleNamespace(text=line))
        return SimpleNamespace(exit_code=0)

    sb.commands.run.side_effect = run
    result = await lease.execute("synthetic multiline output")
    assert result.stdout == "first\n\nsecond\n" and not result.truncated
    await lease.close()


@pytest.mark.asyncio
async def test_native_skills_discovery_downloads_only_verified_release_bytes():
    adapter, sb, journal, admission, factory, release = await provider()
    lease = await adapter.create(scope(), [release])
    backend = OpenSandboxBackend(lease)
    files = await backend.adownload_files(
        [
            "/skills/sample/SKILL.md",
            "/skills/sample/../../etc/passwd",
            "/skills/other/SKILL.md",
        ]
    )
    assert files[0].content == release.contents[0]
    assert files[1].error == files[2].error == "file_not_found"
    sb.commands.run.assert_not_awaited()
    await lease.close()
    with pytest.raises(SandboxFailure, match="UNAVAILABLE"):
        await backend.adownload_files(["/skills/sample/SKILL.md"])


@pytest.mark.asyncio
async def test_cancel_stops_remote_execution_and_failed_kill_remains_pending():
    adapter, sb, journal, admission, factory, release = await provider()
    lease = await adapter.create(scope(), [release])
    started = asyncio.Event()

    async def run(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    sb.commands.run.side_effect = run
    task = asyncio.create_task(lease.execute("sleep 100"))
    await started.wait()
    sb.destroy.side_effect = RuntimeError("service unavailable")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    journal.cleanup_pending.assert_awaited_once()
    journal.released.assert_not_awaited()
    sb.destroy.assert_awaited_once()


@pytest.mark.asyncio
async def test_ambiguous_create_is_not_retried_and_no_host_code_runs():
    adapter, sb, journal, admission, factory, release = await provider()
    factory.side_effect = TimeoutError()
    with pytest.raises(SandboxFailure, match="CREATE_FAILED"):
        await adapter.create(scope(), [release])
    factory.assert_awaited_once()
    sb.commands.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_rejects_cross_origin_redirects_and_large_streams():
    transport = SandboxServiceTransport(
        "http://sandbox.internal:8080",
        inner=httpx.MockTransport(
            lambda request: httpx.Response(
                302, headers={"Location": "http://169.254.169.254/"}
            )
        ),
    )
    with pytest.raises(SandboxFailure, match="ENDPOINT_DENIED"):
        await transport.handle_async_request(
            httpx.Request("GET", "http://169.254.169.254/")
        )
    with pytest.raises(SandboxFailure, match="REDIRECT_DENIED"):
        await transport.handle_async_request(
            httpx.Request("GET", "http://sandbox.internal:8080/test")
        )

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"0123456789"
            yield b"0123456789"

    with pytest.raises(SandboxFailure, match="RESPONSE_LIMIT"):
        async for _ in BoundedResponse(Stream(), limit=15):
            pass


@pytest.mark.asyncio
async def test_native_backend_cannot_write_published_skills_with_root_sdk():
    adapter, sb, journal, admission, factory, release = await provider()
    sb.files.delete_files = AsyncMock()
    lease = await adapter.create(scope(), [release])
    backend = OpenSandboxBackend(lease)
    sb.files.write_files.reset_mock()
    result = await backend.aupload_files(
        [
            ("/skills/sample/SKILL.md", b"overwrite"),
            ("/workspace/../skills/x", b"escape"),
        ]
    )
    assert [item.error for item in result] == ["invalid_path", "invalid_path"]
    sb.files.write_files.assert_not_awaited()
    sb.commands.run.return_value = SimpleNamespace(exit_code=0)
    result = await backend.aupload_files([("/workspace/output/result.txt", b"safe")])
    assert result[0].error is None
    transfer = sb.files.write_files.call_args.args[0][0]
    assert transfer.path.startswith("/opt/open-agent/transfers/")
    assert transfer.owner == "root" and transfer.mode == 444
    assert sb.commands.run.call_args.kwargs["opts"].uid == 1000
    sb.files.delete_files.assert_awaited_once_with([transfer.path])
    await lease.close()


@pytest.mark.asyncio
async def test_native_download_refuses_outside_workspace_and_uses_nonroot_helper():
    adapter, sb, journal, admission, factory, release = await provider()
    lease = await adapter.create(scope(), [release])
    backend = OpenSandboxBackend(lease)
    assert (await backend.adownload_files(["/proc/1/environ"]))[
        0
    ].error == "invalid_path"

    async def run(command, *, opts, handlers):
        assert "O_NOFOLLOW" in command and "dir_fd" in command and opts.uid == 1000
        await handlers.on_stdout(SimpleNamespace(text="aGVsbG8="))
        return SimpleNamespace(exit_code=0)

    sb.commands.run.side_effect = run
    assert (await backend.adownload_files(["/workspace/result.txt"]))[
        0
    ].content == b"hello"
    with pytest.raises(RuntimeError, match="async backend"):
        backend.execute("echo test")
    await lease.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,allow",
    [
        (None, None),
        ("private", "true"),
        ("production", "true"),
        ("development", "false"),
    ],
)
async def test_private_platform_cannot_fall_back_to_public_e2b(
    monkeypatch, mode, allow
):
    from agent.service.plugin.skill_sandbox import (
        E2BSandboxProvider,
        SandboxExecutionRequest,
    )

    for key, value in [
        ("OPEN_AGENT_DEPLOYMENT_MODE", mode),
        ("OPEN_AGENT_ALLOW_PUBLIC_E2B", allow),
    ]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="LEGACY_E2B_DISABLED"):
        await E2BSandboxProvider(None).execute(
            SandboxExecutionRequest(skill_id="legacy", command="echo never-executed")
        )
