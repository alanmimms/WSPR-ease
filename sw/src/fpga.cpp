/*
 * FPGA Control Module Implementation for WSPR-ease
 * Handles iCE40 bitstream loading and SPI register access.
 * Strictly follows Lattice iCE40 SPI Peripheral Mode (Slave SPI) timing.
 */

#include "fpga.hpp"

namespace WSPRRegs {
#include "regs.hpp"
};

#include "filesystem.hpp"
#include "logmanager.hpp"

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/fs/fs.h>

#include <cstring>
#include <errno.h>
#include <stdlib.h>

LOG_MODULE_REGISTER(fpga, LOG_LEVEL_INF);

namespace wspr {

  // Register subsystem with LogManager
  static Logger& logger = LogManager::instance().registerSubsystem("fpga",
								   {"spi", "pps", "config", "bitstream"});

  // GPIO specs from devicetree (All configured as GPIO_ACTIVE_HIGH in overlay)
  static const struct gpio_dt_spec fpgaCRESET = GPIO_DT_SPEC_GET(DT_NODELABEL(fpga_creset), gpios);
  static const struct gpio_dt_spec fpgaDONE = GPIO_DT_SPEC_GET(DT_NODELABEL(fpga_done), gpios);
  static const struct gpio_dt_spec fpgaNCS = GPIO_DT_SPEC_GET(DT_NODELABEL(fpga_ncs), gpios);

  static const struct gpio_dt_spec fpgaNRESET = GPIO_DT_SPEC_GET(DT_NODELABEL(fpga_nreset), gpios);

  static const struct gpio_dt_spec pgFPGACORE = GPIO_DT_SPEC_GET(DT_NODELABEL(pg_fpgacore), gpios);
  static const struct gpio_dt_spec enFPGAIO = GPIO_DT_SPEC_GET(DT_NODELABEL(en_fpgaio), gpios);

  // iCE40 Slave SPI: Mode 0 (CPOL=0, CPHA=0), MSB First.
  static const struct spi_dt_spec fpgaSPI = SPI_DT_SPEC_GET(DT_NODELABEL(fpga_dev),
							    SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB);

  FPGA& FPGA::instance() {
    static FPGA inst;
    return inst;
  }

  int FPGA::init() {
    logger.inf("Initializing FPGA module");

    if (!device_is_ready(fpgaCRESET.port) ||
	!device_is_ready(fpgaCRESET.port) ||
	!device_is_ready(fpgaDONE.port) ||
        !device_is_ready(fpgaNCS.port) ||
	!device_is_ready(pgFPGACORE.port) ||
        !device_is_ready(enFPGAIO.port))
    {
      logger.err("FPGA GPIO devices not ready");
      return -ENODEV;
    }

    if (!spi_is_ready_dt(&fpgaSPI)) {
      logger.err("FPGA SPI device not ready");
      return -ENODEV;
    }

    // MANDATE: Explicitly start with enFPGAIO DEASSERTED (Physical Low).
    // The hardware has a 10k pulldown, but we ensure it in software too.
    gpio_pin_configure_dt(&enFPGAIO, GPIO_OUTPUT_LOW);

    // Hold FPGA in config reset and set CS LOW for SPI slave mode
    gpio_pin_configure_dt(&fpgaNRESET, GPIO_OUTPUT_LOW);
    gpio_pin_configure_dt(&fpgaCRESET, GPIO_OUTPUT_LOW);
    gpio_pin_configure_dt(&fpgaNCS, GPIO_OUTPUT_LOW);
    gpio_pin_configure_dt(&pgFPGACORE, GPIO_INPUT);
    gpio_pin_configure_dt(&fpgaDONE, GPIO_INPUT | GPIO_PULL_UP);

    logger.inf("enFPGAIO deasserted. Waiting for FPGA Core power good (pgFPGACORE)...");

    // Power Sequencing: Wait for Core Power Good
    uint32_t startTime = k_uptime_get_32();
    bool pgOK = false;

    while (!pgOK && k_uptime_get_32() - startTime < 1000) {

      if (gpio_pin_get_dt(&pgFPGACORE) > 0) {
	pgOK = true;
	logger.inf("pgFPGACORE asserted after %u ms", k_uptime_get_32() - startTime);
      }
      k_msleep(5);
    }

    if (!pgOK) {
      logger.wrn("Timeout waiting for pgFPGACORE! Proceeding anyway (Bypass)...");
    }

    // Enable FPGA IO Power
    logger.inf("Enabling FPGA IO power (enFPGAIO)...");
    gpio_pin_set_dt(&enFPGAIO, 1); // Physical High
    k_msleep(100); // Stabilization delay

    // Proceed with configuration
    char bitstreamPath[256];
    snprintf(bitstreamPath, sizeof(bitstreamPath), "%s/fpga.img",
             FileSystem::instance().getMountPoint());

    int ret = loadBitstream(bitstreamPath);
    if (ret < 0) {
      initialized = false;
      return ret;
    }

    initialized = true;
    logger.inf("FPGA initialized and running");
    return 0;
  }

  int FPGA::reset() {
    gpio_pin_set_dt(&fpgaNRESET, 0);	// Assert software reset
    gpio_pin_set_dt(&fpgaNCS, 0);	// SPI slave mode indicator
    gpio_pin_set_dt(&fpgaCRESET, 0);	// Assert FPGA config reset
    k_msleep(1);
    gpio_pin_set_dt(&fpgaCRESET, 1);	// Release FPGA config reset
    k_msleep(2);
    gpio_pin_set_dt(&fpgaNCS, 1);	// Return SPI NCS to deasserted idle state

    if (gpio_pin_get_dt(&fpgaDONE) > 0) {
      logger.err("FPGA Error: CDONE is HIGH immediately after reset pulse!");
      return -EIO;
    }

    // Release software reset when chip is fully operational.
    k_msleep(1);
    gpio_pin_set_dt(&fpgaNRESET, 1);
    return 0;
  }

  int FPGA::loadBitstream(const char* path) {
    struct fs_file_t file;
    fs_file_t_init(&file);

    int ret = fs_open(&file, path, FS_O_READ);
    if (ret < 0) {
      logger.err("bitstream", "Could not open %s: %s", path, strerror(-ret));
      return ret;
    }

    struct fs_dirent stat;
    fs_stat(path, &stat);
    logger.inf("bitstream", "Bitstream %s opened (%zu bytes)", path, (size_t)stat.size);

    const size_t chunk_size = 2048;
    uint8_t* buffer = (uint8_t*)k_malloc(chunk_size);
    if (!buffer) {
      logger.err("bitstream", "Failed to allocate bitstream buffer");
      fs_close(&file);
      return -ENOMEM;
    }

    if (reset() != 0) {
      k_free(buffer);
      fs_close(&file);
      return -EIO;
    }

    gpio_pin_set_dt(&fpgaNCS, 0);
    uint8_t dummy = 0x00;
    struct spi_buf sDummy = { .buf = &dummy, .len = 1 };
    struct spi_buf_set sDummies = { .buffers = &sDummy, .count = 1 };
    spi_write_dt(&fpgaSPI, &sDummies);
    gpio_pin_set_dt(&fpgaNCS, 1);

    logger.inf("bitstream", "Transmitting bitstream...");

    ssize_t bytesRead;
    size_t totalBytes = 0;
    struct spi_buf sBuf = { .buf = buffer, .len = chunk_size };
    struct spi_buf_set sBufs = { .buffers = &sBuf, .count = 1 };

    while ((bytesRead = fs_read(&file, buffer, chunk_size)) > 0) {
      sBuf.len = bytesRead;
      ret = spi_write_dt(&fpgaSPI, &sBufs);
      if (ret < 0) {
	logger.err("bitstream", "SPI write error at %zu: %d", totalBytes, ret);
	k_free(buffer);
	fs_close(&file);
	gpio_pin_set_dt(&fpgaNCS, 1);
	return ret;
      }
      totalBytes += bytesRead;
    }

    fs_close(&file);
    logger.inf("bitstream", "Transmitted %zu bytes", totalBytes);

    gpio_pin_set_dt(&fpgaNCS, 1);

    bool success = false;
    for (int i = 0; i < 100; i++) {

      if (gpio_pin_get_dt(&fpgaDONE) > 0) {
	success = true;
	break;
      }
      spi_write_dt(&fpgaSPI, &sDummies);
    }

    if (success) {
      logger.inf("bitstream", "FPGA SUCCESS: CDONE is HIGH");
      memset(buffer, 0x00, 8);
      sBuf.len = 8;
      spi_write_dt(&fpgaSPI, &sBufs);
      ret = 0;
    } else {
      logger.err("bitstream", "FPGA FAILURE: CDONE remains LOW");
      ret = -EAGAIN;
    }

    k_free(buffer);
    return ret;
  }

  int FPGA::setFrequency(uint32_t freqHz) {
    currentFreq = freqHz;
    if (!initialized) return -ENODEV;

    // NCO tuning for 90 MHz system clock. 
    // The current FPGA Exciter uses phase-to-state mapping where 
    // one full NCO cycle equals one full RF cycle.
    // The NCO increments by M every 90 MHz clock cycle.
    // M = (freqHz / 90,000,000) * 2^32.

    // Using 64-bit math to avoid overflow before division
    uint64_t word = ((uint64_t)freqHz << 32) / ncoHz;

    logger.inf("config", "Setting tuning word to 0x%08X", (uint32_t) word);

    return spiWriteReg(WSPRRegs::aWSPRTuning, (uint32_t)word);
  }

  int FPGA::startTX() {
    if (!initialized) return -ENODEV;
    if (transmitting) return -EALREADY;
    logger.inf("config", "Starting transmission at %u Hz", currentFreq);
    transmitting = true;

    WSPRRegs::WSPRControl ctrl;
    spiReadReg(WSPRRegs::aWSPRControl, &ctrl.u);
    ctrl.txEnable = 1;
    return spiWriteReg(WSPRRegs::aWSPRControl, ctrl.u);
  }

  int FPGA::stopTX() {
    if (!initialized) return -ENODEV;
    if (!transmitting) return 0;
    logger.inf("config", "Stopping transmission");
    transmitting = false;

    WSPRRegs::WSPRControl ctrl;
    spiReadReg(WSPRRegs::aWSPRControl, &ctrl.u);
    ctrl.txEnable = 0;
    return spiWriteReg(WSPRRegs::aWSPRControl, ctrl.u);
  }

  int FPGA::setPowerLevel(uint8_t level) {
    if (!initialized) return -ENODEV;
    logger.inf("config", "Setting FPGA power level to %u", level);

    WSPRRegs::WSPRControl ctrl;
    spiReadReg(WSPRRegs::aWSPRControl, &ctrl.u);
    ctrl.powerThresh = level;
    return spiWriteReg(WSPRRegs::aWSPRControl, ctrl.u);
  }

  int FPGA::sendSymbol(uint8_t symbol) {
    if (!initialized) return -ENODEV;

    // WSPR tone spacing is 1.46484375 Hz (12000 / 8192)
    // All frequency shifts are now handled here.
    double toneFreq = (double)currentFreq + (double)symbol * 1.46484375;
    return setFrequency((uint32_t)toneFreq);
  }

  int FPGA::setLPFBand(WSPRBand band) {
    logger.inf("config", "NOTE: FPGA setLPFBand not yet implemented");
    currentBand = band;
    return 0;
  }

  uint32_t FPGA::getCounter() {
    if (!initialized) return 0;

    WSPRRegs::WSPRPPS val;
    spiReadReg(WSPRRegs::aWSPRPPS, &val.u);
    return val.u;
  }

  int FPGA::spiWriteReg(uint8_t reg, uint32_t value) {
    uint8_t txBuf[5];
    txBuf[0] = 0x80 | (reg & 0x7F);
    txBuf[1] = (value >> 24) & 0xFF;
    txBuf[2] = (value >> 16) & 0xFF;
    txBuf[3] = (value >> 8) & 0xFF;
    txBuf[4] = value & 0xFF;

    gpio_pin_set_dt(&fpgaNCS, 0);
    struct spi_buf sBuf = { .buf = txBuf, .len = sizeof(txBuf) };
    struct spi_buf_set sBufs = { .buffers = &sBuf, .count = 1 };
    int ret = spi_write_dt(&fpgaSPI, &sBufs);
    gpio_pin_set_dt(&fpgaNCS, 1);
    return ret;
  }

  int FPGA::spiReadReg(uint8_t reg, uint32_t* value) {
    uint8_t txBuf[5] = { (uint8_t)(reg & 0x7F), 0, 0, 0, 0 };
    uint8_t rxBuf[5] = { 0 };

    gpio_pin_set_dt(&fpgaNCS, 0);
    struct spi_buf sTX = { .buf = txBuf, .len = 5 };
    struct spi_buf_set sTXs = { .buffers = &sTX, .count = 1 };
    struct spi_buf sRX = { .buf = rxBuf, .len = 5 };
    struct spi_buf_set sRXs = { .buffers = &sRX, .count = 1 };
    int ret = spi_transceive_dt(&fpgaSPI, &sTXs, &sRXs);
    gpio_pin_set_dt(&fpgaNCS, 1);

    if (ret == 0) {
      // Data is in rxBuf[1..4] because Byte 0 was the address/command
      *value = ((uint32_t)rxBuf[1] << 24) |
	((uint32_t)rxBuf[2] << 16) |
	((uint32_t)rxBuf[3] << 8) |
	(uint32_t)rxBuf[4];
    }
    return ret;
  }

} // namespace wspr
