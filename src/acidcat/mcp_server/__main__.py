"""Allow `python -m acidcat.mcp_server`.

The `acidcat-mcp` console script is the documented entry point, but it only
exists on PATH if the venv is active. MCP client configuration (Claude Desktop
and friends) is frequently written as an explicit interpreter plus module,
because that survives not activating anything:

    {"command": "/path/to/venv/bin/python",
     "args": ["-m", "acidcat.mcp_server"]}

Same entry point either way.
"""

import sys

from acidcat.mcp_server.transport import main

if __name__ == "__main__":
    sys.exit(main())
