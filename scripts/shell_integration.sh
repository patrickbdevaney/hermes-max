# hermes-max shell integration — make BARE `hermes` runs visible in the web UI.
#
# This is OPT-IN and is NOT auto-installed into your shell rc. To enable it, add ONE
# line to your ~/.bashrc (or ~/.zshrc):
#
#     source /home/patrickd/hermes-max/scripts/shell_integration.sh
#
# Then any `hermes …` you type in a terminal registers a run descriptor (cwd, prompt,
# mode, pid, livelog offset) so `hm ui` discovers and streams it, and marks it complete
# on exit. Runs launched via `hm run` / `hm dev` register automatically and don't need
# this. The wrapper holds no secrets and never blocks the launch.
#
# To disable: remove the source line (or `unset -f hermes`).

# Resolve the repo root from this file's own location so the wrapper works regardless
# of where hermes-max is checked out.
_HMX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)"

hermes() {
  local _rid=""
  if command -v python3 >/dev/null 2>&1 && [ -f "${_HMX_DIR}/scripts/register_run.py" ]; then
    _rid="$(python3 "${_HMX_DIR}/scripts/register_run.py" "$PWD" "$@" 2>/dev/null)"
  fi
  command hermes "$@"
  local _rc=$?
  if [ -n "${_rid}" ]; then
    python3 "${_HMX_DIR}/scripts/complete_run.py" "${_rid}" 2>/dev/null
  fi
  return ${_rc}
}
