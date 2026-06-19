"""Bridge-matcher harness: ``factories.py`` inserts the handler-shaped rows the indexer
would have written, ``run_*_matching()`` performs one batch-handler pass, and assertions
read the resulting ``bridge_*`` rows back. Shared fixtures (``db``, ``anyio_backend``) live
in the parent ``tests/unit/conftest.py``.
"""
