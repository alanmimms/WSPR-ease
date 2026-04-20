# Check if the script is being executed directly rather than sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Error: This script must be sourced, not executed." >&2
  echo "Usage: source ${0}" >&2
  exit 1
fi

echo Setting up Zephyr environment variables.
export WEST_TOPDIR=~/zephyr-projects
export ZEPHYR_BASE=~/zephyr-projects/zephyr
