#!/usr/bin/env python3
"""Parse mcp-manifest.yaml and emit bash array assignments for scripts/lib.sh.

Stdlib-ONLY (no PyYAML) on purpose: scripts/lib.sh is sourced at the very top of
every hermes-max script, including on a freshly-cloned machine BEFORE bootstrap
has installed anything. The manifest is a constrained YAML subset — a top-level
`servers:` list of flat `key: value` maps — so a ~30-line hand parser is enough
and we never depend on a third-party package just to know the server list.

Reads the manifest path from $HMX_MANIFEST. Emits, for `eval` in bash:

    HMX_SERVERS=(verify rag kg ...)
    HMX_DIR[verify]='mcp-verify'
    HMX_PORTVAR[verify]='MCP_VERIFY_PORT'
    HMX_PORTDEF[verify]='9101'
    HMX_REGISTER_AS[verify]='hermes-max-verify'
    HMX_HEALTH[verify]='/health'

The associative arrays are pre-declared (`declare -A`) by lib.sh before the eval.
"""
import os
import shlex
import sys


def parse(path):
    servers = []
    cur = None
    in_servers = False
    with open(path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # A top-level key (no indent, ends with ':') toggles the servers block.
            if not line.startswith(" ") and stripped.endswith(":"):
                in_servers = stripped == "servers:"
                cur = None
                continue
            if not in_servers:
                continue
            # New list item: "- name: verify" (the rest is parsed as a key:value).
            if stripped.startswith("- "):
                cur = {}
                servers.append(cur)
                stripped = stripped[2:].strip()
            if cur is None:
                continue
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    cur[k] = v
    return servers


def main():
    path = os.environ.get("HMX_MANIFEST")
    if not path or not os.path.isfile(path):
        sys.stderr.write(f"manifest.py: manifest not found: {path!r}\n")
        return 1
    servers = parse(path)
    if not servers:
        sys.stderr.write(f"manifest.py: no servers parsed from {path}\n")
        return 1

    names = [s["name"] for s in servers if s.get("name")]
    out = ["HMX_SERVERS=(%s)" % " ".join(shlex.quote(n) for n in names)]
    for s in servers:
        n = s.get("name")
        if not n:
            continue
        out.append("HMX_DIR[%s]=%s" % (n, shlex.quote(s.get("dir", ""))))
        out.append("HMX_PORTVAR[%s]=%s" % (n, shlex.quote(s.get("port_env", ""))))
        out.append("HMX_PORTDEF[%s]=%s" % (n, shlex.quote(str(s.get("port", "")))))
        out.append(
            "HMX_REGISTER_AS[%s]=%s" % (n, shlex.quote(s.get("register_as", "hermes-max-%s" % n)))
        )
        out.append("HMX_HEALTH[%s]=%s" % (n, shlex.quote(s.get("health", "/health"))))
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
