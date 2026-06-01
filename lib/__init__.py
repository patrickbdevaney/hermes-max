# Marks `lib` as a package so `from lib.inference import run_role` works when the
# repo root is on sys.path. (The bare `import livelog` pattern — lib/ itself on
# sys.path — keeps working regardless; this file only affects `lib.*` imports.)
