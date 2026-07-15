/*
 * CPLD Control Module Implementation for WSPR-ease
 * Handles Lattice MachXO2 CPLD initialization, Si5351 clocks setup, LPF band selection, and SPI registers.
 */

#include "fpga.hpp"
#include "FPGACommon.hpp"
#include "buildNumber.hpp"
#include "regs.hpp"

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
  static Logger& logger = LogManager::instance().registerSubsystem("cpld",
								   {"spi", "config", "si5351"});

  // GPIO specs from devicetree
  static const struct gpio_dt_spec cpldPROG = GPIO_DT_SPEC_GET(DT_NODELABEL(cpld_prog), gpios);
  static const struct gpio_dt_spec cpldDONE = GPIO_DT_SPEC_GET(DT_NODELABEL(cpld_done), gpios);
  static const struct gpio_dt_spec cpldNCS = GPIO_DT_SPEC_GET(DT_NODELABEL(cpld_ncs), gpios);

  static const struct gpio_dt_spec paEN = GPIO_DT_SPEC_GET(DT_NODELABEL(pa_en), gpios);

  static const struct gpio_dt_spec lpfLO = GPIO_DT_SPEC_GET(DT_NODELABEL(lpf_lo), gpios);
  static const struct gpio_dt_spec lpfMID = GPIO_DT_SPEC_GET(DT_NODELABEL(lpf_mid), gpios);
  static const struct gpio_dt_spec lpfHI = GPIO_DT_SPEC_GET(DT_NODELABEL(lpf_hi), gpios);

  // SPI Master: CPOL=0, CPHA=0 (Mode 0), MSB First.
  static const struct spi_dt_spec cpldSPI = SPI_DT_SPEC_GET(DT_NODELABEL(fpga_dev),
							    SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB);

  FPGA& FPGA::instance() {
    static FPGA inst;
    return inst;
  }

  int FPGA::init() {
    logger.inf("Initializing CPLD and Si5351 system");

    if (!device_is_ready(cpldPROG.port) ||
	!device_is_ready(cpldDONE.port) ||
        !device_is_ready(cpldNCS.port) ||
	!device_is_ready(paEN.port) ||
        !device_is_ready(lpfLO.port) ||
        !device_is_ready(lpfMID.port) ||
        !device_is_ready(lpfHI.port))
    {
      logger.err("CPLD/PA/LPF GPIO devices not ready");
      return -ENODEV;
    }

    if (!spi_is_ready_dt(&cpldSPI)) {
      logger.err("CPLD SPI device not ready");
      return -ENODEV;
    }

    // Configure GPIO directions and default values
    gpio_pin_configure_dt(&cpldPROG, GPIO_OUTPUT_HIGH);
    gpio_pin_configure_dt(&cpldNCS, GPIO_OUTPUT_HIGH);
    gpio_pin_configure_dt(&cpldDONE, GPIO_INPUT | GPIO_PULL_UP);

    gpio_pin_configure_dt(&paEN, GPIO_OUTPUT_LOW);
    gpio_pin_configure_dt(&lpfLO, GPIO_OUTPUT_LOW);
    gpio_pin_configure_dt(&lpfMID, GPIO_OUTPUT_LOW);
    gpio_pin_configure_dt(&lpfHI, GPIO_OUTPUT_LOW);

    // 1. Initialize Si5351 first so that CPLD gets clock inputs before starting/exiting reset
    static const struct i2c_dt_spec si5351_i2c = I2C_DT_SPEC_GET(DT_NODELABEL(si5351a));
    if (!si5351.init(&si5351_i2c, tcxoFreqHz)) {
      logger.err("Failed to initialize Si5351 driver");
      return -EIO;
    }

    // Configure default carrier frequency (CLK0 at 6x 14.0956 MHz, CLK2 at 40 MHz)
    currentFreq = static_cast<uint32_t>(WSPRBand::Band20m);
    si5351.setCarrierFreq(currentFreq);
    si5351.setClockOutputsEnabled(true);
    k_msleep(20); // Let clocks stabilize

    // 2. Perform a physical reconfig/refresh on the MachXO2 CPLD
    logger.inf("Toggling CPLD PROG to clear configuration and trigger flash load...");
    gpio_pin_set_dt(&cpldPROG, 0); // Assert refresh/program mode
    k_msleep(5);
    gpio_pin_set_dt(&cpldPROG, 1); // Release to let CPLD load from internal Flash
    k_msleep(20); // Wait for configuration sequence to complete

    if (gpio_pin_get_dt(&cpldDONE) == 0) {
      logger.wrn("CPLD DONE pin remains LOW after configuration boot. Checking register response anyway...");
    } else {
      logger.inf("CPLD DONE asserted HIGH");
    }

    // 3. Verify CPLD communication over SPI via Hardware Signature register
    uint32_t sig = 0;
    if (spiReadReg(aFPGASig, &sig) == 0) {
      if (sig == 0x52505357) {
        logger.inf("CPLD SPI signature verified: 0x%08X", sig);
      } else {
        logger.err("CPLD SPI signature mismatch! Expected 0x52505357, got 0x%08X", sig);
        return -EIO;
      }
    } else {
      logger.err("Failed to read CPLD SPI signature register!");
      return -EIO;
    }

    // Read and verify CPLD build number
    uint32_t buildNum = 0;
    if (spiReadReg(aFPGABuildNo, &buildNum) == 0) {
      logger.inf("CPLD Build Number: %u (expected: %u)", buildNum, fpgaBuildNumber);
    } else {
      logger.err("Failed to read CPLD build number register!");
    }

    // Set default low pass filter band
    setLPFBand(WSPRBand::Band20m);

    initialized = true;
    logger.inf("CPLD and RF clocks fully initialized");
    return 0;
  }

  int FPGA::reset() {
    if (!initialized) return -ENODEV;
    
    // Soft reset CPLD
    triggerSoftReset(true);
    k_msleep(2);
    triggerSoftReset(false);

    // Reset Si5351 PLLs
    si5351.writeRegister(177, 0xA0);
    return 0;
  }

  int FPGA::loadBitstream(const char* path) {
    // MachXO2 is flash-based; SRAM dynamic programming is bypassed in typical runs.
    logger.inf("SRAM bitstream programming not required for MachXO2. Configuration booted from Flash.");
    return 0;
  }

  int FPGA::setFrequency(uint32_t freqHz) {
    if (!initialized) return -ENODEV;
    currentFreq = freqHz;

    logger.inf("config", "Updating carrier frequency to %u Hz (CLK0 output is %u Hz)", freqHz, freqHz * 6);

    // Set Si5351 CLK0 carrier frequency (6x RF base)
    if (!si5351.setCarrierFreq(freqHz)) {
      return -EIO;
    }

    // Write Mode configuration to CPLD Control register based on RF band
    FPGAControl ctrl;
    int ret = spiReadReg(aFPGAControl, &ctrl.u);
    if (ret == 0) {
      // 10 MHz or above uses standard Square wave; below 10 MHz uses 1-2-1 modulated wave
      ctrl.modeSquare = (freqHz >= 10000000) ? 1 : 0;
      ret = spiWriteReg(aFPGAControl, ctrl.u);
    }
    return ret;
  }

  int FPGA::startTX() {
    if (!initialized) return -ENODEV;
    if (transmitting) return -EALREADY;

    logger.inf("config", "Starting RF transmission at %u Hz", currentFreq);
    transmitting = true;

    // Enable power amplifier gate bias/drain power
    gpio_pin_set_dt(&paEN, 1);

    // Assert txEnable bit in CPLD
    FPGAControl ctrl;
    int ret = spiReadReg(aFPGAControl, &ctrl.u);
    if (ret == 0) {
      ctrl.txEnable = 1;
      ctrl.modeSquare = (currentFreq >= 10000000) ? 1 : 0;
      ret = spiWriteReg(aFPGAControl, ctrl.u);
    }
    return ret;
  }

  int FPGA::stopTX() {
    if (!initialized) return -ENODEV;
    if (!transmitting) return 0;

    logger.inf("config", "Stopping RF transmission");
    transmitting = false;

    // Deassert power amplifier enable
    gpio_pin_set_dt(&paEN, 0);

    // Clear txEnable bit in CPLD
    FPGAControl ctrl;
    int ret = spiReadReg(aFPGAControl, &ctrl.u);
    if (ret == 0) {
      ctrl.txEnable = 0;
      ret = spiWriteReg(aFPGAControl, ctrl.u);
    }
    return ret;
  }

  int FPGA::sendSymbol(uint8_t symbol) {
    if (!initialized) return -ENODEV;

    // tone spacing is 1.46484375 Hz
    double toneFreq = (double)currentFreq + (double)symbol * 1.46484375;
    
    // Calculate difference relative to baseline freq in milliHertz
    int32_t milliHzOffset = static_cast<int32_t>((toneFreq - static_cast<double>(currentFreq)) * 1000.0);
    
    // Continuously modulate PLLA multiplier glitchlessly
    si5351.tuneCarrierOffset(milliHzOffset);
    return 0;
  }

  int FPGA::setLPFBand(WSPRBand band) {
    uint32_t freq = static_cast<uint32_t>(band);
    logger.inf("config", "LPF band switched for frequency: %u Hz", freq);

    // Disable all LPF paths first to avoid cross-conduction/spikes
    gpio_pin_set_dt(&lpfLO, 0);
    gpio_pin_set_dt(&lpfMID, 0);
    gpio_pin_set_dt(&lpfHI, 0);

    // Select filter segment
    if (freq <= 4000000) {
      gpio_pin_set_dt(&lpfLO, 1);     // Low filter (<= 4MHz)
    } else if (freq <= 11500000) {
      gpio_pin_set_dt(&lpfMID, 1);    // Mid filter (4 - 11.5MHz)
    } else {
      gpio_pin_set_dt(&lpfHI, 1);     // High filter (11.5 - 32MHz)
    }

    currentBand = band;
    return 0;
  }

  int FPGA::updatePolarMod(uint16_t amplitude, uint16_t phase) {
    if (!initialized) return -ENODEV;

    FPGAPolarMod reg;
    reg.amp = amplitude;
    reg.phase = phase;
    return spiWriteReg(aFPGAPolarMod, reg.u);
  }

  int FPGA::triggerSoftReset(bool assertReset) {
    FPGAControl ctrl;
    int ret = spiReadReg(aFPGAControl, &ctrl.u);
    if (ret == 0) {
      ctrl.softReset = assertReset ? 1 : 0;
      ret = spiWriteReg(aFPGAControl, ctrl.u);
    }
    return ret;
  }

  int FPGA::spiWriteReg(uint8_t reg, uint32_t value) {
    uint8_t txBuf[5];
    txBuf[0] = 0x80 | (reg & 0x7F);
    txBuf[1] = (value >> 24) & 0xFF;
    txBuf[2] = (value >> 16) & 0xFF;
    txBuf[3] = (value >> 8) & 0xFF;
    txBuf[4] = value & 0xFF;

    gpio_pin_set_dt(&cpldNCS, 0);
    struct spi_buf sBuf = { .buf = txBuf, .len = sizeof(txBuf) };
    struct spi_buf_set sBufs = { .buffers = &sBuf, .count = 1 };
    int ret = spi_write_dt(&cpldSPI, &sBufs);
    gpio_pin_set_dt(&cpldNCS, 1);
    return ret;
  }

  int FPGA::spiReadReg(uint8_t reg, uint32_t* value) {
    uint8_t txBuf[5] = { (uint8_t)(reg & 0x7F), 0, 0, 0, 0 };
    uint8_t rxBuf[5] = { 0 };

    gpio_pin_set_dt(&cpldNCS, 0);
    struct spi_buf sTX = { .buf = txBuf, .len = 5 };
    struct spi_buf_set sTXs = { .buffers = &sTX, .count = 1 };
    struct spi_buf sRX = { .buf = rxBuf, .len = 5 };
    struct spi_buf_set sRXs = { .buffers = &sRX, .count = 1 };
    int ret = spi_transceive_dt(&cpldSPI, &sTXs, &sRXs);
    gpio_pin_set_dt(&cpldNCS, 1);

    if (ret == 0) {
      *value = ((uint32_t)rxBuf[1] << 24) |
	((uint32_t)rxBuf[2] << 16) |
	((uint32_t)rxBuf[3] << 8) |
	(uint32_t)rxBuf[4];
    }
    return ret;
  }

} // namespace wspr
