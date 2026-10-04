# BOM change review example

- Python 3.11+ standard library only for the core comparator/workflows. Zoe approved the official MCP SDK and its necessary dependencies for the optional MCP boundary on 2026-09-30; use the pinned `mcp` extra and lockfile. Run core tests with `python -m unittest discover -s tests -v`.
- Run the lesson with `python lesson1.py --original LM5155 --candidate LM51551`.
- Runtime reads `data/catalog.json`, never test answers or private resume material.
- Preserve source IDs, conditions and exact model identity. A passing feature check is not whole-circuit approval.
- Public source facts and synthetic engineering requirements must remain distinguishable.
- Do not claim database, MCP, EDA, simulation or hardware verification from local tests.
- Work on an isolated branch. No publishing, hardware operations or credentials are needed for lesson 1.
- Record verification under `.spec/lesson-01/`; use the existing deterministic cases as the independent oracle.
