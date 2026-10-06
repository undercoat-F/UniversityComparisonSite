import unittest
from types import SimpleNamespace
from unittest.mock import patch

from benchmark.neon_branch import cmd_reset, cmd_setup, endpoint_id_from_host
from benchmark.prepare_seeds import endpoint_id


class FakeApi:
    def __init__(self, branches):
        self.branches = branches
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("/branches"):
            return {"branches": self.branches}
        if method == "GET" and path.endswith("/endpoints"):
            return {"endpoints": [{"type": "read_write", "host": "ep-x.neon.tech"}]}
        if method == "POST" and path.endswith("/branches"):
            new = {"id": f"br-{body['branch']['name']}", "name": body["branch"]["name"],
                   "parent_id": body["branch"]["parent_id"]}
            self.branches.append(new)
            return {"branch": new, "operations": []}
        return {"operations": []}

    def wait_operations(self, project_id, operations):
        pass


PROD = {"id": "br-prod", "name": "production", "parent_id": None}
BASE = {"id": "br-base", "name": "bench-base", "parent_id": "br-prod"}
RUN = {"id": "br-run", "name": "bench-run", "parent_id": "br-base"}


class TestNeonBranch(unittest.TestCase):
    def test_endpoint_id_ignores_pooler_suffix(self):
        self.assertEqual(endpoint_id_from_host("ep-abc-123-pooler.ap-southeast-1.aws.neon.tech"), "ep-abc-123")
        self.assertEqual(endpoint_id("ep-abc-123.ap-southeast-1.aws.neon.tech"), "ep-abc-123")

    def test_setup_creates_base_from_production_and_run_from_base(self):
        api = FakeApi([dict(PROD)])

        cmd_setup(api, "proj", "br-prod", SimpleNamespace(cu=1.0))

        posts = [body for method, path, body in api.calls if method == "POST"]
        self.assertEqual([p["branch"] for p in posts], [
            {"name": "bench-base", "parent_id": "br-prod"},
            {"name": "bench-run", "parent_id": "br-bench-base"},
        ])
        self.assertEqual(posts[1]["endpoints"][0]["autoscaling_limit_min_cu"], 1.0)
        self.assertEqual(posts[1]["endpoints"][0]["autoscaling_limit_max_cu"], 1.0)

    def test_setup_is_idempotent(self):
        api = FakeApi([dict(PROD), dict(BASE), dict(RUN)])

        cmd_setup(api, "proj", "br-prod", SimpleNamespace(cu=None))

        self.assertFalse([c for c in api.calls if c[0] == "POST"])

    def test_reset_restores_run_from_base(self):
        api = FakeApi([dict(PROD), dict(BASE), dict(RUN)])

        cmd_reset(api, "proj", "br-prod", SimpleNamespace())

        self.assertIn(("POST", "/projects/proj/branches/br-run/restore", {"source_branch_id": "br-base"}), api.calls)

    def test_reset_refuses_when_run_is_not_a_child_of_base(self):
        api = FakeApi([dict(PROD), dict(BASE), {**RUN, "parent_id": "br-prod"}])

        with self.assertRaises(SystemExit):
            cmd_reset(api, "proj", "br-prod", SimpleNamespace())
        self.assertFalse([c for c in api.calls if c[0] == "POST"])


class TestLoadTargets(unittest.TestCase):
    def test_does_not_fall_back_to_urls_txt_when_db_has_no_seeds(self):
        # seed テーブルの設定ミスで本物の大学サイト（URLs.txt）をクロールしないこと
        from ControlPlane import schedular

        with patch.object(schedular, "load_targets_from_db", return_value=[]):
            self.assertEqual(schedular.load_targets(), [])
        self.assertFalse(hasattr(schedular, "load_targets_from_txt"))


if __name__ == "__main__":
    unittest.main()
