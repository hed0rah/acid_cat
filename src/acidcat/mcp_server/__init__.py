"""acidcat-mcp -- stdio / streamable-HTTP MCP server over the per-library
sample index.

Split into handlers (tool implementations + data access), schema (registration),
and transport (dispatch + servers + CLI entry). This module re-exports the
stable surface; importing it registers all tools.
"""

from acidcat.mcp_server.handlers import ToolError, infer_kind  # noqa: F401
from acidcat.mcp_server.schema import TOOLS, _nullable_optionals  # noqa: F401
from acidcat.mcp_server.transport import _jsonable, dispatch, main  # noqa: F401

__all__ = ["TOOLS", "dispatch", "main", "ToolError", "infer_kind"]
