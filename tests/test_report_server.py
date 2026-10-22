from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from eval_tool.report_server import ManualJobs, Reports


class _FakeJobs:
    def __init__(self):
        self.started: list[tuple[str, str | None]] = []

    def start(self, kind: str, agent: str | None = None) -> bool:
        self.started.append((kind, agent))
        return True

    def status(self) -> dict:
        return {"status": "idle"}


class ReportServerTests(unittest.TestCase):
    def setUp(self):
        self.jobs = _FakeJobs()
        Reports.jobs = self.jobs
        Reports.allowed_origins = frozenset({"http://localhost:3000"})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Reports)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_allowed_grafana_origin_can_start_healthcheck(self):
        request = Request(
            self.base + "/actions/healthcheck",
            method="POST",
            headers={"Origin": "http://localhost:3000", "Content-Type": "application/json"},
            data=b"{}",
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:3000")
            self.assertTrue(json.load(response)["accepted"])
        self.assertEqual(self.jobs.started, [("healthcheck", None)])

    def test_can_start_healthcheck_for_one_agent(self):
        request = Request(
            self.base + "/actions/healthcheck/leave",
            method="POST",
            headers={"Origin": "http://localhost:3000", "Content-Type": "application/json"},
            data=b"{}",
        )
        with urlopen(request) as response:
            payload = json.load(response)
            self.assertEqual(response.status, 202)
            self.assertEqual(payload["agent"], "leave")
        self.assertEqual(self.jobs.started, [("healthcheck", "leave")])

    def test_grafana_action_preflight_allows_its_request_headers(self):
        request = Request(
            self.base + "/actions/healthcheck",
            method="OPTIONS",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-grafana-action,x-grafana-device-id",
            },
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:3000")
            allowed = {item.strip().lower() for item in response.headers["Access-Control-Allow-Headers"].split(",")}
            self.assertTrue({"content-type", "x-grafana-action", "x-grafana-device-id"} <= allowed)

    def test_other_origins_are_rejected(self):
        request = Request(
            self.base + "/actions/evaluation",
            method="POST",
            headers={"Origin": "https://example.test"},
            data=b"{}",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request)
        self.assertEqual(raised.exception.code, 403)
        self.assertEqual(self.jobs.started, [])

    def test_health_endpoint_needs_no_report_password(self):
        with urlopen(self.base + "/healthz") as response:
            self.assertEqual(json.load(response), {"status": "ok"})

    def test_latest_run_endpoint_redirects_to_resolved_target(self):
        Reports.config = Mock()
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = build_opener(NoRedirect)
        with patch(
            "eval_tool.report_server._latest_run_target",
            return_value="http://localhost:3000/d/ta-run-detail/run-detail?var-run_id=run-123",
        ):
            with self.assertRaises(HTTPError) as raised:
                opener.open(self.base + "/latest/healthcheck/leave/run")
        self.assertEqual(raised.exception.code, 302)
        self.assertEqual(
            raised.exception.headers["Location"],
            "http://localhost:3000/d/ta-run-detail/run-detail?var-run_id=run-123",
        )


class ManualJobsTests(unittest.TestCase):
    def test_single_agent_healthcheck_only_runs_requested_agent(self):
        called = []
        config = Mock(agents={"leave": Mock(), "late": Mock()})

        def single_agent_job(_config, agents=None):
            called.append(agents)
            return {"total": len(agents or []), "errors": 0}

        jobs = ManualJobs(config)
        with patch("eval_tool.report_server.run_healthchecks", single_agent_job):
            self.assertTrue(jobs.start("healthcheck", "leave"))
            for _ in range(100):
                if jobs.status()["status"] != "running":
                    break
                threading.Event().wait(0.01)
        self.assertEqual(called, [["leave"]])
        self.assertEqual(jobs.status()["total"], 1)

    def test_second_job_is_rejected_while_first_is_running(self):
        release = threading.Event()

        def slow_job(_config):
            release.wait(2)
            return {"total": 4, "errors": 0}

        jobs = ManualJobs(Mock())
        with patch("eval_tool.report_server.run_healthchecks", slow_job):
            self.assertTrue(jobs.start("healthcheck"))
            self.assertFalse(jobs.start("evaluation"))
            release.set()
            for _ in range(100):
                if jobs.status()["status"] != "running":
                    break
                threading.Event().wait(0.01)
        self.assertEqual(jobs.status()["status"], "completed")


if __name__ == "__main__":
    unittest.main()
