"""Kept so older commands still work: same as `python -m evals.run_eval --agent baseline`."""

import asyncio

from evals.run_eval import main

if __name__ == "__main__":
    asyncio.run(main(default_agent="baseline"))
