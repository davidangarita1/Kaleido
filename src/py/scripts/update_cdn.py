# /// script
# requires-python = ">=3.12"
# dependencies = [
#  "aiohttp",
#  "changelogtxt_parser @ git+https://github.com/geopozo/changelogtxt-parser",
#  "jq",
#  "semver",
# ]
# ///

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import aiohttp
import changelogtxt_parser as changelog
import jq
import semver

from py.kaleido._page_generator import DEFAULT_PLOTLY
from py.kaleido._page_generator import __file__ as FILE_PATH

REPO = os.environ["REPO"]
GITHUB_WORKSPACE = os.environ["GITHUB_WORKSPACE"]


async def run(commands: list[str]) -> tuple[bytes, bytes, int | None]:
    p = await asyncio.create_subprocess_exec(
        *commands, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return (*(await p.communicate()), p.returncode)


async def verify_url(url: str) -> bool:
    async with aiohttp.ClientSession() as session:
        async with session.head(url) as response:
            return response.status == 200


async def get_latest_version() -> str:
    out, err, _ = await run(["gh", "api", "repos/plotly/plotly.js/tags", "--paginate"])
    tags = jq.compile('map(.name | ltrimstr("v"))').input_value(json.loads(out)).first()
    versions = [semver.VersionInfo.parse(v) for v in tags]
    if err:
        print(err.decode())
        sys.exit(1)
    return str(max(versions))


async def create_pr(latest_version: str) -> None:
    branch = f"bot/update-cdn-{latest_version}"
    title = f"Update Plotly.js CDN to v{latest_version}"
    body = f"This PR updates the CDN URL to v{latest_version}."

    _, err, brc_eval = await run(
        ["gh", "api", f"repos/{REPO}/branches/{branch}", "--silent"]
    )
    branch_exists = (brc_eval == 0)

    if branch_exists:
        print(f"The branch {branch} already exists", file=sys.stderr)
        sys.exit(1)
    else:
        msg = err.decode()
        if "HTTP 404" not in msg:
            print(msg, file=sys.stderr)  # unexpected errors
            sys.exit(1)

    pr, _, _ = await run(
        ["gh", "pr", "list", "-R", REPO, "-H", branch, "--state", "all"]
    )

    if pr.decode():
        print(f"Pull request for '{branch}' already exists", file=sys.stderr)
        sys.exit(1)

    file_updated = changelog.update(latest_version, title, GITHUB_WORKSPACE)

    if not file_updated:
        print("Failed to update changelog", file=sys.stderr)
        sys.exit(1)

    await run(["git", "checkout", "-b", branch])
    await run(["git", "add", "."])
    await run(
        [
            "git",
            "-c",
            "user.name='github-actions'",
            "-c",
            "user.email='github-actions@github.com'",
            "commit",
            "-m",
            f"chore: {title}",
        ]
    )
    _, push_err, push_eval = await run(["git", "push", "-u", "origin", branch])

    if push_eval:
        print(push_err.decode(), file=sys.stderr)
        sys.exit(1)

    new_pr, pr_err, pr_eval = await run(
        ["gh", "pr", "create", "-B", "master", "-H", branch, "-t", title, "-b", body]
    )
    if pr_eval:
        print(pr_err.decode(), file=sys.stderr)
        sys.exit(1)

    print("Pull request:", new_pr.decode().strip())


async def verify_issue(title: str) -> None:
    issue, _, _ = await run(
        [
            "gh",
            "issue",
            "list",
            "-R",
            REPO,
            "--search",
            title,
            "--state",
            "all",
            "--json",
            "number,state",
        ]
    )
    issues = json.loads(issue.decode())
    if issues:
        for issue in issues:
            if issue.get("state") == "OPEN":
                print(f"Issue '{title}' already exists in:")
                print(f"https://github.com/{REPO}/issues/{issue.get('number')}")
                sys.exit(1)
        print(f"Issue '{title}' is closed")
        sys.exit(0)


async def create_issue(title: str, body: str) -> None:
    new_issue, issue_err, _ = await run(
        ["gh", "issue", "create", "-R", REPO, "-t", title, "-b", body]
    )
    if issue_err:
        print(issue_err.decode())
        sys.exit(1)

    print(f"The issue '{title}' was created in {new_issue.decode().strip()}")


async def main() -> None:
    latest_version = await get_latest_version()
    new_cdn = f"https://cdn.plot.ly/plotly-{latest_version}.js"

    if new_cdn == DEFAULT_PLOTLY:
        print("Already up to date")
        sys.exit(0)

    cdn_exists = await verify_url(new_cdn)
    if cdn_exists:
        p = Path(FILE_PATH)
        s = p.read_text(encoding="utf-8").replace(DEFAULT_PLOTLY, new_cdn, 1)
        p.write_text(s, encoding="utf-8")

        await create_pr(latest_version)
    else:
        title = f"CDN not reachable for Plotly.js v{latest_version}"
        body = f"URL: {new_cdn} - invalid url"

        await verify_issue(title)
        await create_issue(title, body)


asyncio.run(main())
