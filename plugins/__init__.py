# plugins — optional capabilities. None of these touch core logic; the conductor
# and the MCP servers have ZERO knowledge of them. They register against the one
# generic hook the conductor exposes, or they don't (see load_plugins.py).
