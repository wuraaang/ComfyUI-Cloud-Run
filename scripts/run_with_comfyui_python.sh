#!/bin/sh
set -eu

python_command=${PYTHON_COMMAND:-python3}
comfyui_python_command=${COMFYUI_PYTHON_COMMAND:-}

if [ -n "$comfyui_python_command" ]; then
  command -v "$comfyui_python_command" >/dev/null 2>&1 || {
    echo "[comfyui-python] configured Python command not found" >&2
    exit 1
  }
elif command -v "$python_command" >/dev/null 2>&1 && \
  "$python_command" -c 'import PIL' >/dev/null 2>&1; then
  comfyui_python_command=$python_command
elif [ -n "${HOME:-}" ]; then
  for comfyui_python_candidate in \
    "$HOME/ComfyUI-Installs/ComfyUI/standalone-env/bin/python3.13" \
    "$HOME/ComfyUI-Installs/ComfyUI/standalone-env/bin/python3"
  do
    if [ -x "$comfyui_python_candidate" ] && \
      "$comfyui_python_candidate" -c 'import PIL' >/dev/null 2>&1; then
      comfyui_python_command=$comfyui_python_candidate
      break
    fi
  done
fi

if [ -z "$comfyui_python_command" ] || \
  ! "$comfyui_python_command" -c 'import PIL' >/dev/null 2>&1; then
  echo "[comfyui-python] ComfyUI Python with Pillow not found; set COMFYUI_PYTHON_COMMAND" >&2
  exit 1
fi

exec "$comfyui_python_command" "$@"
