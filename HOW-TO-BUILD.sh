. SETUP-ENV.bash
make -C FPGA && west build -p always -b esp32s3_devkitm/esp32s3/procpu sw && tools/wspr-flash.sh

# Then on a machine directly connected to the target:
# cd sw && . ./.venv/bin/activate && ../tools/flash-lfs.sh && ../tools/monitor-console.sh
