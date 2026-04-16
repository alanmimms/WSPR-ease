make -C FPGA && west build -p always -b esp32s3_devkitm/esp32s3/procpu sw && tools/wspr-flash.sh
