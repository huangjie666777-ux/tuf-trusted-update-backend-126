"""Generate the demo signing repository under demo/repo/.

Usage: .venv/bin/python demo/make_repo.py
Then serve it:  .venv/bin/python -m http.server 8001 --directory demo/repo
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo.repolib import RepoBuilder


def main() -> None:
    repo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repo")
    repo = RepoBuilder(repo_dir)
    repo.add_target("app/hello.txt", b"hello from the trusted repo\n")
    repo.add_target("app/version.json", b'{"version": "1.0.0"}\n')
    repo.build()
    print(f"demo repository written to {repo_dir}")
    print(f"trust anchor (distribute out-of-band): {repo_dir}/metadata/1.root.json")


if __name__ == "__main__":
    main()
