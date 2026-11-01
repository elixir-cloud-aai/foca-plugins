"""Tests of `scripts/release.py`."""

from email.message import Message
from http.client import RemoteDisconnected
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError

import pytest

from scripts import new_plugin, release
from tests.helpers import DISTANCE, META_VERSION, git, set_extras

META_TAG = f"foca-plugins-v{META_VERSION}"


def test_release(repo: Path) -> None:
    """Tags select the package of their distribution and version.

    Args:
        repo: Repository root.
    """
    new_plugin.create("tes", "routing", "distance", repo)
    assert release.release("foca-plugin-tes-routing-distance-v0.1.0", repo) == {
        "path": DISTANCE.path,
        "name": DISTANCE.name,
        "version": "0.1.0",
    }
    assert release.release(META_TAG, repo)["path"] == "meta"


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("v0.1.0", "is no tag"),
        ("foca-plugin-other-v0.1.0", "is no tag"),
        ("foca-plugins-0.2.0", "is no tag"),
        ("foca-plugins-v0.2.0-dev0", "is no tag"),
        ("foca-plugins-v0.3.0", f"has version '{META_VERSION}' on this commit"),
        ("foca-plugin-generic-example-noop-v0.1.0", "is never published"),
    ],
)
def test_release_refused(repo: Path, tag: str, message: str) -> None:
    """Malformed tags, other versions and the example are refused.

    Args:
        repo: Repository root.
        tag: Tag.
        message: Expected error message.
    """
    with pytest.raises(ValueError, match=re.escape(message)):
        release.release(tag, repo)


def test_notes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The notes of a tag are its version's section of the changelog.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    changelog = repo / "meta" / "CHANGELOG.md"
    tag = META_TAG
    section = re.escape(f"## {META_VERSION}")
    with pytest.raises(ValueError, match=f"no sections '{section}'"):
        release.notes(tag, repo)
    changelog.write_text(f"## {META_VERSION}\n\nA.\n\n## {META_VERSION} (2)\n\nB.\n")
    with pytest.raises(ValueError, match=f"has 2 sections '{section}'"):
        release.notes(tag, repo)
    changelog.write_text(f"# Changelog\n\n## {META_VERSION}\n\n## 0.1.0\n\nOld\n")
    with pytest.raises(ValueError, match="is empty"):
        release.notes(tag, repo)
    changelog.write_text(
        f"# Changelog\n\n## {META_VERSION} (2026-10-25)\n\nIntro.\n\n"
        "### Features\n\n- New (#1, #2)\n\n## 0.1.0\n\nOld\n"
    )
    assert release.notes(tag, repo) == "Intro.\n\n## Features\n\n- New (#1, #2)\n"
    changelog.write_text(f"## {META_VERSION}\n\nLast.\n")
    assert release.main(["notes", tag], repo) == 0
    assert capsys.readouterr().out == "Last.\n"


def test_notes_format(repo: Path) -> None:
    """The grouped sections become `##` sections, in the order of the format.

    Args:
        repo: Repository root.
    """
    changelog = repo / "meta" / "CHANGELOG.md"

    def notes(section: str) -> str:
        changelog.write_text(f"# Changelog\n\n## {META_VERSION}\n\n{section}")
        return release.notes(META_TAG, repo)

    sections = "".join(f"### {name}\n\n- A (#1)\n\n" for name in release.SECTIONS)
    assert (
        notes(f"Intro.\n\n{sections}")
        == "Intro.\n\n" + sections.replace("### ", "## ").rstrip("\n") + "\n"
    )
    # Deeper headings move up as well; code blocks and issue numbers stay
    text = (
        "Intro, see\n#12.\n\n### Fixes\n\n#### Plugin\n\n```toml\n### x\n```\n\n"
        "~~~\n# y\n~~~\n"
    )
    assert notes(text) == (
        "Intro, see\n#12.\n\n## Fixes\n\n### Plugin\n\n```toml\n### x\n```\n\n"
        "~~~\n# y\n~~~\n"
    )
    # Indented headings and fences (up to 3 spaces), closing sequences of
    # headings, inline code that looks like a fence, and fences that only
    # close with a line of at least as many of their characters
    text = (
        "Intro, `x`\n```a```.\n\n  ### Fixes ##\n\n````\n```\n    ````\n````\n\n"
        " ~~~\n### x\n   ~~~  \n\n- B (#2)\n"
    )
    assert notes(text) == (
        "Intro, `x`\n```a```.\n\n  ## Fixes ##\n\n````\n```\n    ````\n````\n\n"
        " ~~~\n### x\n   ~~~  \n\n- B (#2)\n"
    )
    # Lines of `-` or `=` after other lines than paragraph text are thematic
    # breaks or text, not setext headings
    text = (
        "Intro.\n\n### Fixes\n---\n- A (#1)\n---\n```\nx\n```\n---\n> q\n===\n\n"
        "- B (#2)\n"
    )
    assert notes(text) == text.replace("### ", "## ")


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("### Features\n\n- A (#1)\n", "has no intro paragraph"),
        ("Intro.\n\n### Changes\n\n- A (#1)\n", "unknown section 'Changes'"),
        (
            "Intro.\n\n### Fixes\n\n- A (#1)\n\n### Features\n\n- B (#2)\n",
            "'Features' after 'Fixes'",
        ),
        (
            "Intro.\n\n### Fixes\n\n- A (#1)\n\n### Fixes\n\n- B (#2)\n",
            "'Fixes' after 'Fixes'",
        ),
        ("Intro.\n\n# Title\n", "heading '# Title'"),
        ("Intro.\n\n### C#\n", "unknown section 'C#'"),
        ("Intro.\n\n#### Fixes\n\n- A (#1)\n", "outside a grouped section"),
        ("Intro.\nTitle\n---\n", "heading 'Title'"),
        ("Intro.\n\n### Fixes\n\nTitle\n=\n", "heading 'Title'"),
        ("Intro.\n\n```\n### Fixes\n", "unclosed code block"),
    ],
)
def test_notes_format_refused(repo: Path, section: str, message: str) -> None:
    """Sections without intro, with unknown or unordered sections are refused.

    Args:
        repo: Repository root.
        section: Section of the version, without its heading.
        message: Expected error message.
    """
    changelog = repo / "meta" / "CHANGELOG.md"
    changelog.write_text(f"# Changelog\n\n## {META_VERSION}\n\n{section}")
    with pytest.raises(ValueError, match=re.escape(message)):
        release.notes(META_TAG, repo)


class Response:
    """A response of PyPI, as a context manager (cf. `urlopen()`)."""

    def __enter__(self) -> "Response":
        """Enter the context.

        Returns:
            The response.
        """
        return self

    def __exit__(self, *args: object) -> None:
        """Leave the context.

        Args:
            args: Exception, if any.
        """


class FakePyPI:
    """A fake `urlopen()` of PyPI's JSON API, which records its calls.

    Attributes:
        published: URLs of the published releases.
        error: Error raised for any other URL; HTTP 404 by default.
        calls: URLs and timeouts of the calls.
    """

    def __init__(self, published: set[str], error: Exception | None = None) -> None:
        """Set up the fake.

        Args:
            published: URLs of the published releases.
            error: Error raised for any other URL; HTTP 404 by default.
        """
        self.published = published
        self.error = error
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> Response:
        """Open a URL.

        Args:
            url: URL.
            timeout: Timeout in seconds.

        Returns:
            A response, if the URL is of a published release.

        Raises:
            HTTPError: HTTP 404, unless another error is set.
            Exception: The error that is set.
        """
        self.calls.append((url, timeout))
        if url in self.published:
            return Response()
        raise self.error or HTTPError(url, 404, "Not Found", Message(), None)


A_URL = "https://pypi.org/pypi/foca-plugin-tes-routing-a/1.0.0/json"
B_URL = "https://pypi.org/pypi/foca-plugin-tes-routing-b/0.2.0rc1/json"


def test_unpublished(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The meta distribution's pins that are not on PyPI are listed.

    Args:
        repo: Repository root.
        monkeypatch: Patches.
    """
    a = "foca-plugin-tes-routing-a==1.0.0"
    b = "foca-plugin-tes-routing-b==0.2.0rc1"
    set_extras(repo, f'tes = ["{a}", "{b}"]\nall = ["{a}"]\nexperimental = ["{b}"]')
    urlopen = FakePyPI({A_URL})
    monkeypatch.setattr(release, "urlopen", urlopen)
    assert release.unpublished(META_TAG, repo) == [b]
    # Each pin once, with a timeout
    assert [url for url, _ in urlopen.calls] == [A_URL, B_URL]
    assert all(timeout > 0 for _, timeout in urlopen.calls)
    # Names and versions can't change the path or add a query
    set_extras(repo, 'tes = ["foca-plugin-tes-routing-a==1/../2?x"]')
    assert release.unpublished(META_TAG, repo) == [
        "foca-plugin-tes-routing-a==1/../2?x"
    ]
    assert urlopen.calls[-1][0] == (
        "https://pypi.org/pypi/foca-plugin-tes-routing-a/1%2F..%2F2%3Fx/json"
    )
    # Plugins pin nothing
    new_plugin.create("tes", "routing", "distance", repo)
    assert release.unpublished("foca-plugin-tes-routing-distance-v0.1.0", repo) == []


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("u", 503, "Service Unavailable", Message(), None),
        URLError("no network"),
        TimeoutError("timed out"),
        RemoteDisconnected("closed"),
    ],
)
def test_unpublished_errors(
    repo: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Other errors than HTTP 404 are refused, not taken as "unpublished".

    Args:
        repo: Repository root.
        monkeypatch: Patches.
        error: Error of PyPI.
    """
    set_extras(repo, 'tes = ["foca-plugin-tes-routing-a==1.0.0"]')
    monkeypatch.setattr(release, "urlopen", FakePyPI(set(), error))
    with pytest.raises(ValueError, match="Can't check foca-plugin-tes-routing-a"):
        release.unpublished(META_TAG, repo)


def test_main_pins(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The `pins` command fails if a pinned version is not on PyPI.

    Args:
        repo: Repository root.
        monkeypatch: Patches.
        capsys: Captured output.
    """
    monkeypatch.setattr(release, "urlopen", FakePyPI(set()))
    assert release.main(["pins", META_TAG], repo) == 0
    set_extras(repo, 'tes = ["foca-plugin-tes-routing-a==1.0.0"]')
    assert release.main(["pins", META_TAG], repo) == 1
    assert capsys.readouterr().err == (
        "::error::Not on PyPI yet: foca-plugin-tes-routing-a==1.0.0. Release"
        " these plugins first, then re-run the failed jobs\n"
    )
    outage = HTTPError(A_URL, 503, "Service Unavailable", Message(), None)
    monkeypatch.setattr(release, "urlopen", FakePyPI(set(), outage))
    assert release.main(["pins", META_TAG], repo) == 1
    assert "::error::Can't check foca-plugin-tes-routing-a" in capsys.readouterr().err


def test_main(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The `release` command prints the package, or why the tag is refused.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    assert release.main(["release", META_TAG], repo) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "foca-plugins"
    assert release.main(["release", "x"], repo) == 1
    assert "::error::'x' is no tag" in capsys.readouterr().err
    assert release.main(["previous", META_TAG, "x"], repo) == 0
    assert capsys.readouterr().out == "\n"


def test_previous(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The previous tag is the package's newest among the commit's ancestors.

    Args:
        tmp_path: Repository.
        capsys: Captured output.
    """
    name = "foca-plugin-tes-a"
    git(tmp_path, "init", "-q")

    def commit(*tags: str) -> str:
        git(tmp_path, "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "c")
        for tag in tags:
            git(tmp_path, "tag", "--no-sign", tag)
        return git(tmp_path, "rev-parse", "HEAD")

    first = commit(f"{name}-v0.1.0")
    second = commit(f"{name}-b-v1.0.0", f"{name}-v0.2.0rc1")
    third = commit(f"{name}-v0.2.0")
    assert release.previous(f"{name}-v0.1.0", first, tmp_path) is None
    assert release.previous(f"{name}-v0.2.0rc1", second, tmp_path) == f"{name}-v0.1.0"
    assert release.previous(f"{name}-v0.2.0", third, tmp_path) == f"{name}-v0.2.0rc1"
    assert release.previous(f"{name}-b-v1.0.1", third, tmp_path) == f"{name}-b-v1.0.0"
    commit(f"{name}-v1.0-x")
    fifth = commit()
    with pytest.raises(ValueError, match="Unexpected previous tag"):
        release.previous(f"{name}-v1.0.0", fifth, tmp_path)
    assert release.main(["previous", f"{name}-v1.0.0", fifth], tmp_path) == 1
    assert "Unexpected previous tag" in capsys.readouterr().err
