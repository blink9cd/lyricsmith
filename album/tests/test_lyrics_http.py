"""The LRC providers' shared HTTP: the User-Agent, the per-request timeout, the JSON read.

No test here reaches the network: requests go through a real session whose transport
answers from memory (`http_fakes`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import requests
import requests.adapters

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above

#: A reserved name the transport fake answers, so nothing is ever resolved.
URL = "https://example.invalid/api"
#: The name every request carries.
_TOOL = "lyricsmith"


class UserAgentTest(fixtures.IsolatedCase):
    """The tool's name and the project's version, read from the project's config."""

    def write_config(self, name: str, text: str) -> Path:
        """A config file *name* holding *text*, under the test's own directory."""
        config = self.tmp / name
        config.write_bytes(text.encode("utf-8"))
        return config

    def test_the_version_comes_from_the_projects_config(self) -> None:
        config = self.write_config("ok.toml", '[project]\nversion = "9.8.7"\n')
        self.assertEqual(lh.user_agent(config), f"{_TOOL}/9.8.7")

    def test_the_agent_names_lyricsmith_wherever_the_folder_lives(self) -> None:
        """The name is a constant now: a folder rename must not change what servers see."""
        config = self.write_config("named.toml", '[project]\nversion = "0.1.0"\n')
        self.assertEqual(lh.user_agent(config), "lyricsmith/0.1.0")
        self.assertEqual(lh.USER_AGENT_NAME, "lyricsmith")

    def test_an_unreadable_config_gives_an_unknown_version(self) -> None:
        configs = {
            "absent": self.tmp / "absent.toml",
            "not toml": self.write_config("bad.toml", "[project\n"),
            "no version": self.write_config("none.toml", '[project]\nname = "x"\n'),
            "project not a table": self.write_config("flat.toml", "project = 3\n"),
            "version not text": self.write_config(
                "int.toml", "[project]\nversion = 3\n"
            ),
            "not utf-8": self.tmp / "bytes.toml",
        }
        configs["not utf-8"].write_bytes(b"\xff\xfe")
        for case, config in configs.items():
            with self.subTest(case=case):
                self.assertEqual(lh.user_agent(config), f"{_TOOL}/unknown")

    def test_each_half_keeps_only_safe_characters(self) -> None:
        config = self.write_config(
            "odd.toml", '[project]\nversion = "1.2\\r\\nX: é 3"\n'
        )
        self.assertEqual(lh.user_agent(config, "my tool\n"), "mytool/1.2X3")

    def test_a_half_with_nothing_safe_left_falls_back(self) -> None:
        config = self.write_config("blank.toml", '[project]\nversion = "é \\n"\n')
        cases = {"empty name": "", "non-ascii name": "日本", "only a newline": "\n"}
        for case, name in cases.items():
            with self.subTest(case=case):
                self.assertEqual(
                    lh.user_agent(config, name),
                    f"{lh.FALLBACK_TOOL_NAME}/{lh.UNKNOWN_VERSION}",
                )

    def test_a_normal_name_and_version_pass_unchanged(self) -> None:
        config = self.write_config("fine.toml", '[project]\nversion = "2.0.1-rc_1"\n')
        self.assertEqual(lh.user_agent(config, "a.b-c_d"), "a.b-c_d/2.0.1-rc_1")

    def test_the_real_config_names_a_version(self) -> None:
        self.assertRegex(lh.user_agent(), rf"^{_TOOL}/\d+\.\d+")

    def test_a_new_session_sends_the_user_agent(self) -> None:
        seen: list[str] = []

        def answer(request: requests.PreparedRequest) -> requests.Response:
            seen.append(str(request.headers["User-Agent"]))
            return http_fakes.response(request, 200, b"{}")

        with lh.new_session() as session, http_fakes.transport(answer):
            lh.get_json(session, URL, {}, lm.Source.LRCLIB)
        self.assertEqual(seen, [lh.user_agent()])


class GetJsonTest(fixtures.IsolatedCase):
    """One GET: its JSON and its URL, None for an absent status, an error otherwise."""

    def _get(
        self, answer: http_fakes.Answer, absent: frozenset[int] = frozenset()
    ) -> lh.Reply | None:
        """GET URL with one parameter, answered by *answer*."""
        with lh.new_session() as session, http_fakes.transport(answer):
            return lh.get_json(
                session, URL, {"q": "a b"}, lm.Source.NETEASE, absent=absent
            )

    def test_the_body_and_the_url_asked(self) -> None:
        def answer(request: requests.PreparedRequest) -> requests.Response:
            return http_fakes.response(request, 200, http_fakes.json_body({"k": [1]}))

        self.assertEqual(self._get(answer), lh.Reply(f"{URL}?q=a+b", {"k": [1]}))

    def test_an_absent_status_is_none(self) -> None:
        self.assertIsNone(self._get(http_fakes.always(404), frozenset({404})))

    def test_an_error_status_raises(self) -> None:
        with self.assertRaises(lp.HttpStatusError):
            self._get(http_fakes.always(404))

    def test_a_body_that_is_not_json_names_the_source(self) -> None:
        def answer(request: requests.PreparedRequest) -> requests.Response:
            return http_fakes.response(request, 200, b"\xff not json")

        with self.assertRaises(lp.ProviderError) as raised:
            self._get(answer)
        self.assertEqual(
            str(raised.exception), "NetEase answered a reply that is not JSON"
        )

    def test_every_request_carries_the_timeout(self) -> None:
        seen: list[object] = []

        def send(
            adapter: requests.adapters.HTTPAdapter,
            request: requests.PreparedRequest,
            **kwargs: object,
        ) -> requests.Response:
            seen.append(kwargs.get("timeout"))
            return http_fakes.response(request, 200, b"[]")

        with (
            lh.new_session() as session,
            mock.patch.object(requests.adapters.HTTPAdapter, "send", send),
        ):
            lh.get_json(session, URL, {}, lm.Source.LRCLIB)
        self.assertEqual(seen, [lh.REQUEST_TIMEOUT_S])
