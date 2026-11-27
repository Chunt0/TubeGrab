"""bin/grab, the command-line client, against the real routes (no server, no downloads)."""
import importlib.machinery
import importlib.util
import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import patch

_path = Path(__file__).resolve().parent.parent / "bin" / "grab"
_loader = importlib.machinery.SourceFileLoader("grab", str(_path))
_spec = importlib.util.spec_from_loader("grab", _loader)
grab = importlib.util.module_from_spec(_spec)
_loader.exec_module(grab)


class TTY(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def server(client, monkeypatch):
    """urlopen answered by the Flask app; jobs are recorded, not run."""
    started = []
    patch(monkeypatch, "_run_job", lambda *a: started.append(a))

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def urlopen(req, timeout=None):
        path = req.full_url.split("8765", 1)[1]
        r = client.open(path, method=req.get_method(), data=req.data, content_type="application/json")
        if r.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, r.status_code, "", {}, io.BytesIO(r.data))
        return Resp(r.data)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return started


def run(*argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    code = grab.main(["--server", "http://nas:8765", *argv],
                     stdin=io.StringIO(stdin) if stdin else TTY(), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_queues_every_link_in_a_file(server, tmp_path):
    f = tmp_path / "links.txt"
    f.write_text("https://youtu.be/aaaaaaaaaaa\n\n# later\n  https://youtu.be/bbbbbbbbbbb  \n"
                 "https://youtu.be/aaaaaaaaaaa\n")
    code, out, _ = run("-v", "-p", str(f))
    assert code == 0
    assert [a[1:] for a in server] == [("https://youtu.be/aaaaaaaaaaa", "video", True),
                                       ("https://youtu.be/bbbbbbbbbbb", "video", True)]
    assert "2 queued" in out


def test_defaults_to_single_audio_and_takes_links_and_stdin(server):
    code, _, _ = run("https://youtu.be/aaaaaaaaaaa", "-", stdin="https://youtu.be/bbbbbbbbbbb\n")
    assert code == 0
    assert [a[1:] for a in server] == [("https://youtu.be/aaaaaaaaaaa", "audio", False),
                                       ("https://youtu.be/bbbbbbbbbbb", "audio", False)]


def test_pipe_without_arguments(server):
    assert run(stdin="https://youtu.be/aaaaaaaaaaa\n")[0] == 0
    assert len(server) == 1


def test_a_refused_link_does_not_stop_the_rest(server, tmp_path):
    f = tmp_path / "links.txt"
    f.write_text("youtu.be/no-scheme\nhttps://youtu.be/aaaaaaaaaaa\n")
    code, out, _ = run(str(f))
    assert code == 1
    assert "URL must start with" in out
    assert len(server) == 1


def test_no_links(server, tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("# nothing yet\n")
    assert run(str(f))[0] == 2
    assert run(str(tmp_path / "missing.txt"))[0] == 2
    assert server == []


def test_unreachable_server(monkeypatch):
    def urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    code, _, err = run("https://youtu.be/aaaaaaaaaaa")
    assert code == 2 and "can't reach" in err


def test_wait_reports_each_job(server, monkeypatch):
    from tubegrab import config
    monkeypatch.setattr(grab, "_sleep", lambda s: None)
    real_call = grab.call

    def call(srv, path, body=None):   # the jobs finish as soon as they're asked about
        if path == "/api/jobs":
            for j, status in zip(sorted(config._jobs.values(), key=lambda j: j["url"]), ("complete", "error")):
                j.update(status=status, title=j["url"][-3:], error="Video unavailable" if status == "error" else None)
        return real_call(srv, path, body)

    monkeypatch.setattr(grab, "call", call)
    code, out, _ = run("-w", "https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb")
    assert code == 1
    assert "✓  aaa" in out and "✗  bbb: Video unavailable" in out


# ---------------------------------------------------------------- --slow

@pytest.fixture
def slow(server, monkeypatch):
    """--slow against the routes: each link's jobs end as `outcomes[link]` says,
    in turn (default: complete); records the pauses."""
    from tubegrab import config
    outcomes, sleeps = {}, []
    monkeypatch.setattr(grab, "_sleep", sleeps.append)
    real_call = grab.call

    def call(srv, path, body=None):
        if path == "/api/jobs":
            for j in list(config._jobs.values()):
                if j["status"] == "queued":
                    status, error = (outcomes.get(j["url"]) or [("complete", None)]).pop(0)
                    if status == "lost":
                        del config._jobs[j["id"]]
                    else:
                        j.update(status=status, error=error, title=j["url"][-3:], entry_count=1,
                                 finished_at="2026-01-01T00:00:00+00:00")
        return real_call(srv, path, body)

    monkeypatch.setattr(grab, "call", call)
    return outcomes, sleeps


LIMITED = "[youtube] x: This content isn't available, try again later."


def test_slow_goes_one_at_a_time_and_a_rerun_skips_what_finished(slow, server, tmp_path):
    outcomes, sleeps = slow
    f = tmp_path / "albums.txt"
    f.write_text("https://youtu.be/aaa\nhttps://youtu.be/bbb\nhttps://youtu.be/ccc\n")
    outcomes["https://youtu.be/bbb"] = [("error", "Video unavailable")]
    code, out, _ = run("-p", "--slow", "--pause", "30", str(f))
    assert code == 1
    assert [a[1] for a in server] == ["https://youtu.be/aaa", "https://youtu.be/bbb", "https://youtu.be/ccc"]
    assert sleeps.count(30) == 2                                  # between links, not before the first
    assert (tmp_path / "albums.txt.done").read_text() == "https://youtu.be/aaa\nhttps://youtu.be/ccc\n"
    assert "1 failed" in out and "Video unavailable" in out

    server.clear()
    code, out, _ = run("-p", "--slow", str(f))
    assert [a[1] for a in server] == ["https://youtu.be/bbb"]     # only the one that didn't finish
    assert "2 already done, 1 to go" in out and code == 0

    server.clear()
    assert run("--slow", str(f))[0] == 0 and server == []


def test_slow_waits_out_youtubes_limit_and_tries_again(slow, server):
    outcomes, sleeps = slow
    outcomes["https://youtu.be/aaa"] = [("error", LIMITED), ("complete", LIMITED), ("complete", None)]
    code, out, _ = run("--slow", "https://youtu.be/aaa")
    assert code == 0
    assert len(server) == 3                       # failed, then cut short ("complete" with the error), then done
    assert sleeps.count(grab.LIMIT_WAIT) == 2
    assert "✓  aaa" in out


def test_slow_gives_up_on_a_limit_that_doesnt_lift(slow, server):
    outcomes, sleeps = slow
    outcomes["https://youtu.be/aaa"] = [("error", LIMITED)] * 10
    code, out, _ = run("--slow", "https://youtu.be/aaa", "https://youtu.be/bbb")
    assert code == 1
    assert len(server) == grab.LIMIT_TRIES + 2    # tried LIMIT_TRIES more times, then on to the next
    assert "1 done, 1 failed" in out


def test_slow_restarts_a_job_the_server_forgot_and_waits_for_the_server(slow, server, monkeypatch):
    outcomes, sleeps = slow
    outcomes["https://youtu.be/aaa"] = [("lost", None)]     # a deploy restarted the server mid-download
    real_call, down = grab.call, [2]

    def flaky(srv, path, body=None):
        if path == "/api/jobs" and down[0]:
            down[0] -= 1
            raise urllib.error.URLError("connection refused")
        return real_call(srv, path, body)

    monkeypatch.setattr(grab, "call", flaky)
    code, out, _ = run("--slow", "https://youtu.be/aaa")
    assert code == 0 and len(server) == 2
    assert sleeps.count(60) == 2 and out.count("can't reach") == 1
