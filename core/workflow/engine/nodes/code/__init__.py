"""
Code execution node module for workflow engine.

This module provides code execution capabilities within workflow nodes,
supporting isolated execution environments including the built-in LangChain
Pyodide sandbox, E2B, and iFly remote execution services. In-process local
execution is intentionally not supported.
"""
