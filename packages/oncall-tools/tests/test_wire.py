"""What survives the boundary between an agent and a tool server.

Every other test in this package either reaches a server in-process or calls
its functions directly. Both are the right default: they are fast, and they run
real servers rather than mocks. But two bugs hid in precisely the gap they
leave, and 371 passing tests saw neither.

  * A REFUSAL'S REASON. `_apply` composes "this tool accepts ['off'] for
    'paymentFailure'" -- the one sentence that would let a model correct itself.
    The SDK withheld it, because it routes anything that is not an `mcp`
    `ToolError` through `UnexpectedToolError`, whose contract is that a crash's
    text stays on the server. Six `pytest.raises(ActionNotAllowed)` tests passed
    the whole time: they call `_apply`, so nothing crossed the wire.

  * `FLAGD_CONFIG`. The stdio client forwards only HOME, LOGNAME, PATH, SHELL,
    TERM and USER unless `env` is passed, so a spawned server fell back to
    `DEFAULT_FLAG_PATH` and wrote the COMMITTED baseline instead of the live
    document -- reporting `changed: True` either way. Every existing test runs
    the server in this process, where `os.environ` is simply shared.

So these tests go the long way round on purpose.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from mcp.client import Client
from oncall_contracts.flagd import current_variant, read_document
from oncall_tools.registry import SERVERS, TRANSPORT_ENV, ToolCallError, _stdio_env, load_tools
from oncall_tools.servers.ops_actions import FLAG_PATH_ENV, build_server

REPO = Path(__file__).resolve().parents[3]
REAL = REPO / "deploy" / "flagd" / "demo.flagd.json"


@pytest.fixture
def live_document(tmp_path, monkeypatch):
    """An ARMED copy of the real document, which the committed baseline never is.

    Armed is what makes the stdio test safe to run while the bug is present: a
    server that reads the baseline instead finds `paymentFailure` already `off`,
    so the fix is a no-op and nothing is written. The test fails on this copy
    being untouched rather than by editing a tracked file.
    """
    path = tmp_path / "demo.flagd.json"
    shutil.copy(REAL, path)
    document = json.loads(path.read_text())
    document["flags"]["paymentFailure"]["defaultVariant"] = "100%"
    path.write_text(json.dumps(document, indent=2))
    monkeypatch.setenv(FLAG_PATH_ENV, str(path))
    return path


@pytest.fixture
def ops(live_document):
    """The write server in this process. The refusal tests need a real client
    but not a real subprocess: the wrapping happens in the tool layer."""
    with load_tools(servers=["ops_actions"], connect=lambda _s: Client(build_server())) as loaded:
        yield loaded


# --- a refusal has to explain itself ------------------------------------


def test_a_refused_fix_tells_the_client_which_variants_it_would_accept(ops):
    """The correction signal, which is the whole point of refusing rather than
    failing silently. Without it a model can only retry the same call."""
    with pytest.raises(ToolCallError, match=r"\['off'\]"):
        ops["set_flag"].invoke({"flag": "paymentFailure", "variant": "100%"})


def test_a_refused_fix_names_the_flag_and_the_variant(ops):
    with pytest.raises(ToolCallError) as caught:
        ops["set_flag"].invoke({"flag": "paymentFailure", "variant": "100%"})

    assert "paymentFailure" in str(caught.value)
    assert "100%" in str(caught.value)


def test_a_flag_no_runbook_knows_is_refused_by_name(ops):
    """No variants to offer here, so the message is shorter -- but it still has
    to say which flag, or the agent cannot tell a typo from a policy."""
    with pytest.raises(ToolCallError, match="emitRawPii"):
        ops["set_flag"].invoke({"flag": "emitRawPii", "variant": "off"})


def test_a_rollback_refusal_explains_itself_too(ops):
    """`rollback_flag` refuses every fix pair. Same wrapping, different tool."""
    with pytest.raises(ToolCallError, match=r"\['100%'\]"):
        ops["rollback_flag"].invoke({"flag": "paymentFailure", "variant": "off"})


# --- what a spawned server inherits -------------------------------------


def test_a_server_gets_the_environment_it_declares(monkeypatch):
    monkeypatch.setenv(FLAG_PATH_ENV, "/somewhere/demo.flagd.json")

    assert _stdio_env(SERVERS["ops_actions"])[FLAG_PATH_ENV] == "/somewhere/demo.flagd.json"


def test_a_server_does_not_get_environment_it_never_declared(monkeypatch):
    """Least privilege, and a real hazard: the read server has no business
    knowing where the writable flag document lives."""
    monkeypatch.setenv(FLAG_PATH_ENV, "/somewhere/demo.flagd.json")

    assert FLAG_PATH_ENV not in _stdio_env(SERVERS["runbooks"])


def test_an_unset_variable_is_absent_rather_than_empty(monkeypatch):
    """`FLAGD_CONFIG=""` would defeat `os.environ.get(...) or DEFAULT`, which is
    the one line this whole mechanism exists to feed."""
    monkeypatch.delenv(FLAG_PATH_ENV, raising=False)

    assert FLAG_PATH_ENV not in _stdio_env(SERVERS["ops_actions"])


def test_the_spec_and_the_server_name_the_same_variable():
    """`SERVERS` spells "FLAGD_CONFIG" rather than importing it, so that
    importing the registry does not drag in the corpus reader. This is the cost
    of that: a test, instead of the compiler, catching a rename."""
    assert FLAG_PATH_ENV in SERVERS["ops_actions"].env


def test_the_child_still_gets_a_path_to_find_python_on(monkeypatch):
    """The spawn is `python -m ...`. Replacing the environment wholesale instead
    of extending the SDK's default would leave nothing to resolve that against."""
    assert _stdio_env(SERVERS["ops_actions"])["PATH"]


# --- the whole way round ------------------------------------------------


def test_an_approved_write_over_stdio_edits_the_live_document(live_document, monkeypatch):
    """The one test in this repo that spawns a server as a subprocess.

    `monkeypatch.chdir` because the child inherits the parent's cwd and the
    corpus is read relative to it -- this test should not depend on where pytest
    was invoked. The committed baseline is hashed, not trusted: if this ever
    writes the wrong file, the assertion names the file rather than leaving a
    tracked change behind for someone to find later.
    """
    monkeypatch.chdir(REPO)
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)
    baseline = hashlib.sha256(REAL.read_bytes()).hexdigest()

    with load_tools(servers=["ops_actions"]) as registry:
        result = registry["set_flag"].invoke({"flag": "paymentFailure", "variant": "off"})

    assert result["changed"] is True
    assert result["previous"] == "100%"
    assert current_variant(read_document(live_document), "paymentFailure") == "off"
    assert hashlib.sha256(REAL.read_bytes()).hexdigest() == baseline, (
        f"the server wrote {REAL}, the committed baseline, instead of the live document"
    )
