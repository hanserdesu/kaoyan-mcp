# -*- coding: utf-8 -*-
"""MCP 协议测试：initialize / tools / resources / prompts / 错误码。"""
import io
import json
import unittest

from tests.util import demo_workspace
from kaoyan_mcp.server import MCPServer


def call(server, messages, ws=None):
    stdin = io.StringIO("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages))
    out = io.StringIO()
    err = io.StringIO()
    srv = MCPServer(ws or demo_workspace(), stdin, out, err)
    srv.serve_forever()
    return [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


class ProtocolTest(unittest.TestCase):
    def test_initialize_and_ping(self):
        ws = demo_workspace()
        rep = call(None, [
            dict(jsonrpc="2.0", id=1, method="initialize",
                 params=dict(protocolVersion="2025-06-18", capabilities={},
                             clientInfo=dict(name="t", version="0"))),
            dict(jsonrpc="2.0", id=2, method="ping"),
        ], ws)
        self.assertEqual(len(rep), 2)
        init = rep[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["serverInfo"]["name"], "kaoyan-mcp")
        self.assertIn("tools", init["capabilities"])
        self.assertIn("instructions", init)
        self.assertEqual(rep[1]["result"], {})

    def test_unknown_protocol_version_gets_supported_one(self):
        ws = demo_workspace()
        rep = call(None, [dict(jsonrpc="2.0", id=1, method="initialize",
                               params=dict(protocolVersion="1999-01-01"))], ws)
        self.assertIn(rep[0]["result"]["protocolVersion"], ("2025-06-18", "2025-03-26", "2024-11-05"))

    def test_tools_list_has_schemas(self):
        ws = demo_workspace()
        rep = call(None, [dict(jsonrpc="2.0", id=1, method="tools/list")], ws)
        tools = rep[0]["result"]["tools"]
        names = [t["name"] for t in tools]
        for want in ("env_capabilities", "kb_validate", "review_due", "review_grade",
                     "paper_deliver", "learn_predict", "learn_report"):
            self.assertIn(want, names)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["description"])

    def test_tools_call_validate(self):
        ws = demo_workspace()
        rep = call(None, [dict(jsonrpc="2.0", id=1, method="tools/call",
                               params=dict(name="kb_validate", arguments={}))], ws)
        res = rep[0]["result"]
        self.assertFalse(res["isError"])
        self.assertIn("PASS", res["content"][0]["text"])

    def test_tools_call_unknown_is_error_content(self):
        ws = demo_workspace()
        rep = call(None, [dict(jsonrpc="2.0", id=1, method="tools/call",
                               params=dict(name="nope", arguments={}))], ws)
        self.assertTrue(rep[0]["result"]["isError"])

    def test_tool_exception_becomes_error_content_not_crash(self):
        ws = demo_workspace()
        rep = call(None, [dict(jsonrpc="2.0", id=1, method="tools/call",
                               params=dict(name="paper_deliver", arguments={}))], ws)
        self.assertTrue(rep[0]["result"]["isError"])
        self.assertIn("spec", rep[0]["result"]["content"][0]["text"])

    def test_resources(self):
        ws = demo_workspace()
        rep = call(None, [
            dict(jsonrpc="2.0", id=1, method="resources/list"),
            dict(jsonrpc="2.0", id=2, method="resources/read", params=dict(uri="kb://index")),
            dict(jsonrpc="2.0", id=3, method="resources/read", params=dict(uri="kb://nope")),
        ], ws)
        uris = [r["uri"] for r in rep[0]["result"]["resources"]]
        self.assertIn("kb://index", uris)
        self.assertTrue(any(u.startswith("kb://chapter/") for u in uris))
        self.assertIn("状态统计", rep[1]["result"]["contents"][0]["text"])
        self.assertEqual(rep[2]["result"]["contents"], [])

    def test_prompts(self):
        ws = demo_workspace()
        rep = call(None, [
            dict(jsonrpc="2.0", id=1, method="prompts/list"),
            dict(jsonrpc="2.0", id=2, method="prompts/get", params=dict(name="teaching")),
        ], ws)
        names = [p["name"] for p in rep[0]["result"]["prompts"]]
        self.assertIn("teaching", names)
        self.assertIn("grading", names)
        text = rep[1]["result"]["messages"][0]["content"]["text"]
        self.assertIn("先考后讲", text)

    def test_unknown_method_and_notification(self):
        ws = demo_workspace()
        rep = call(None, [
            dict(jsonrpc="2.0", id=1, method="does/not/exist"),
            dict(jsonrpc="2.0", method="notifications/initialized"),
        ], ws)
        self.assertEqual(len(rep), 1, "通知不该产生响应")
        self.assertEqual(rep[0]["error"]["code"], -32601)

    def test_bad_json_line(self):
        ws = demo_workspace()
        stdin = io.StringIO("{not json}\n")
        out = io.StringIO()
        MCPServer(ws, stdin, out, io.StringIO()).serve_forever()
        rep = json.loads(out.getvalue().strip())
        self.assertEqual(rep["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()

