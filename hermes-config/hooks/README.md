# hermes-config/hooks

Optional Hermes hooks (a native surface — `hooks:` in config.yaml). These are
**opt-in**; the verify gate itself is enforced by the `workflow-task-finish`
skill, not a hook.

## block-destructive.sh

A `pre_tool_call` hook that vetoes obviously-catastrophic terminal commands
(`rm -rf /`, `mkfs`, `dd of=/dev/...`, fork bombs, `chmod -R 777 /`, force-push
to main/master). Belt-and-suspenders for unattended overnight autonomy. Fails
**open** (allows) on any error, so a hook bug never wedges the agent.

### Enable

```bash
mkdir -p ~/.hermes/agent-hooks
cp hermes-config/hooks/block-destructive.sh ~/.hermes/agent-hooks/
chmod +x ~/.hermes/agent-hooks/block-destructive.sh
```

Then add to `~/.hermes/config.yaml`:

```yaml
hooks:
  pre_tool_call:
    - matcher: "terminal"
      command: "~/.hermes/agent-hooks/block-destructive.sh"
      timeout: 5
```

Hermes will prompt for first-use consent (or set `hooks_auto_accept: true` /
run with `--accept-hooks` for unattended setup).
