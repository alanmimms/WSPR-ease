#!/bin/bash
# remote-flash.sh - Executed on alanm to flash on nano1
# $1 will be the path to the bin file passed by west

TARGET_IP="nano1.local"
TARGET_PORT="/dev/ttyACM0"
TARGET_BIN=/homealan/ham/WSPR-ease/$1

echo "--- Redirecting Flash to ${TARGET_IP} ---"

# Path to the venv site-packages on the remote side
REMOTE_VENV_LIB="/homealan/zephyr-projects/.venv/lib/python3.12/site-packages"

ssh ${TARGET_IP} "PYTHONPATH=${REMOTE_VENV_LIB} python3 -m \
  esptool --chip esp32s3 \
  --baud 921600 \
  --port ${TARGET_PORT} \
  --baud 921600 \
  write-flash 0x0 ${TARGET_BIN}"
