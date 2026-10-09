"""python -m musician.analyze <repo_path> [--diff]"""
from src.analyze.__main__ import build_parser, main  # noqa: F401

if __name__ == "__main__":
    main()
