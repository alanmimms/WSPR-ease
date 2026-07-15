#include "Si5351.hpp"
#include <zephyr/sys/util.h>
#include <zephyr/logging/log.h>
#include <string.h>

LOG_MODULE_REGISTER(si5351, LOG_LEVEL_INF);

Si5351::Si5351() {
  tcxoFreqHz = 0;
  pllaFreqHz = 0;
  pllbFreqHz = 800000000; // 800 MHz fixed PLL B
  rfBaseFreqHz = 0;
  rfOffsetMilliHz = 0;

  for (int i = 0; i < 8; ++i) {
    msInteger[i] = 0;
    msNumerator[i] = 0;
    msDenominator[i] = max20BitValue;
  }
}

Si5351::~Si5351() {}

bool Si5351::init(const struct i2c_dt_spec *spec, uint32_t tcxoFreq) {
  i2cSpec = spec;
  if (!i2cSpec || !i2c_is_ready_dt(i2cSpec)) {
    LOG_ERR("I2C bus device not ready");
    return false;
  }

  tcxoFreqHz = tcxoFreq;

  // 1. Disable all clock outputs during initialization (Reg 3 = 0xFF)
  writeRegister(regOutputControl, 0xFF);

  // 2. Power down and disable unused clocks
  for (uint8_t i = 0; i < 8; ++i) {
    writeRegister(regCLKControlBase + i, 0x80);
  }

  // 3. Disable OEB hardware pins
  writeRegister(9, 0xFF);

  // 4. Set crystal load capacitance to 0 pF for external TCXO
  writeRegister(regCrystalLoad, 0x12);

  // 5. Disable spread spectrum
  writeRegister(149, 0x00);

  // 6. Setup PLL B to a fixed 800 MHz (using 20MHz TCXO reference)
  // Multiplier = 40 (pure integer for lowest noise)
  setupPLLB(pllbFreqHz, tcxoFreqHz);

  // 7. Setup CLK2 to output exactly 40 MHz from PLL B (pure integer division: 800 / 40 = 20)
  msInteger[2] = 20;
  msNumerator[2] = 0;
  msDenominator[2] = max20BitValue;
  updateMultiSynthDividers(2);
  
  // CLK2 control: source PLL B (0x20), powered up, 8mA drive strength (0x03) -> 0x23
  writeRegister(regCLKControlBase + 2, 0x23);

  // 8. Reset PLLs to align phase
  writeRegister(regPLLReset, 0xA0);

  initialized = true;
  LOG_INF("Si5351 init success. TCXO=%u Hz. PLL B=800MHz, CLK2=40MHz (PWM clock).", tcxoFreqHz);
  return true;
}

bool Si5351::setCarrierFreq(uint32_t rfFreqHz) {
  if (!initialized) return false;
  rfBaseFreqHz = rfFreqHz;

  // CLK0 needs to output 6x the RF carrier frequency for 1-2-1 sequence
  uint32_t clk0FreqHz = rfFreqHz * 6;

  // Select a MultiSynth divider N for CLK0 (even integer, multiple of 6) to keep PLLA VCO in 600-900MHz range
  uint32_t bestN = 10;
  uint32_t targetVCO = 800000000;
  uint32_t minDiff = 0xFFFFFFFF;

  for (uint32_t candidate = 6; candidate <= 150; candidate += 2) {
    uint64_t vco = static_cast<uint64_t>(candidate) * clk0FreqHz;
    if (vco >= 600000000ULL && vco <= 900000000ULL) {
      uint32_t diff = (vco > targetVCO) ? static_cast<uint32_t>(vco - targetVCO)
                                        : static_cast<uint32_t>(targetVCO - vco);
      if (diff < minDiff) {
        minDiff = diff;
        bestN = candidate;
      }
    }
  }

  pllaFreqHz = bestN * clk0FreqHz;

  // Setup PLL A to lock to the calculated VCO frequency (fractional setup for exact freq)
  setupPLLA(pllaFreqHz, tcxoFreqHz);

  // Setup MultiSynth 0 (CLK0) to divide by bestN (pure integer, b=0) to eliminate phase noise
  msInteger[0] = bestN;
  msNumerator[0] = 0;
  msDenominator[0] = max20BitValue;
  updateMultiSynthDividers(0);

  // CLK0 control: source PLL A (0x00), powered up, 8mA drive strength (0x03) -> 0x03
  writeRegister(regCLKControlBase + 0, 0x03);

  // Reset PLL A to lock alignment
  writeRegister(regPLLReset, 0x20);

  LOG_INF("CLK0 set to 6x carrier: %u Hz (RF=%u Hz). PLLA=%u Hz, Divider=%u.", 
          clk0FreqHz, rfFreqHz, pllaFreqHz, bestN);
  return true;
}

void Si5351::tuneCarrierOffset(int32_t milliHzOffset) {
  if (!initialized || rfBaseFreqHz == 0) return;
  rfOffsetMilliHz = milliHzOffset;

  // Shift CLK0 by 6x the RF offset
  int32_t clk0OffsetMilliHz = milliHzOffset * 6;

  // Modulate PLLA frequency atomically over I2C without reset!
  int64_t pllOffsetMilliHz = static_cast<int64_t>(msInteger[0]) * clk0OffsetMilliHz;
  int64_t targetPLLMilliHz = static_cast<int64_t>(pllaFreqHz) * 1000LL + pllOffsetMilliHz;

  if (targetPLLMilliHz <= 0 || tcxoFreqHz == 0) return;

  uint64_t refMilliHz = static_cast<uint64_t>(tcxoFreqHz) * 1000ULL;
  uint32_t a_pll = static_cast<uint32_t>(targetPLLMilliHz / refMilliHz);
  uint64_t rem = static_cast<uint64_t>(targetPLLMilliHz % refMilliHz);
  uint32_t b_pll = static_cast<uint32_t>((rem * max20BitValue) / refMilliHz);
  uint32_t c_pll = max20BitValue;

  writeSynthParams(regPLLABase, a_pll, b_pll, c_pll);
}

void Si5351::setClockOutputsEnabled(bool enabled) {
  // CLK0 and CLK2 enabled (bits 0 and 2 cleared in Reg 3) -> 0xFA, otherwise 0xFF
  writeRegister(regOutputControl, enabled ? 0xFA : 0xFF);
}

void Si5351::writeRegister(uint8_t reg, uint8_t data) {
  if (i2cSpec && i2c_is_ready_dt(i2cSpec)) {
    i2c_reg_write_byte_dt(i2cSpec, reg, data);
  }
}

uint8_t Si5351::readRegister(uint8_t reg) {
  uint8_t data = 0;
  if (i2cSpec && i2c_is_ready_dt(i2cSpec)) {
    i2c_write_read_dt(i2cSpec, &reg, 1, &data, 1);
  }
  return data;
}

void Si5351::setupPLLA(uint32_t targetPLLFreqHz, uint32_t refFreqHz) {
  if (refFreqHz == 0) return;
  uint32_t a = targetPLLFreqHz / refFreqHz;
  uint64_t rem = targetPLLFreqHz % refFreqHz;
  uint32_t b = static_cast<uint32_t>((rem * max20BitValue) / refFreqHz);
  uint32_t c = max20BitValue;

  writeSynthParams(regPLLABase, a, b, c);
}

void Si5351::setupPLLB(uint32_t targetPLLFreqHz, uint32_t refFreqHz) {
  if (refFreqHz == 0) return;
  uint32_t a = targetPLLFreqHz / refFreqHz;
  uint64_t rem = targetPLLFreqHz % refFreqHz;
  uint32_t b = static_cast<uint32_t>((rem * max20BitValue) / refFreqHz);
  uint32_t c = max20BitValue;

  writeSynthParams(regPLLBBase, a, b, c);
}

void Si5351::updateMultiSynthDividers(uint8_t clk) {
  if (clk > 7) return;
  uint8_t baseReg = regMultiSynthBase + (clk * 8);
  writeSynthParams(baseReg, msInteger[clk], msNumerator[clk], msDenominator[clk]);
}

void Si5351::writeSynthParams(uint8_t baseReg, uint32_t multOrDiv, uint32_t num, uint32_t denom, bool divBy4) {
  if (denom == 0) denom = 1;

  uint32_t p1 = 128 * multOrDiv + static_cast<uint32_t>((128ULL * num) / denom) - 512;
  uint32_t p2 = static_cast<uint32_t>(128ULL * num - denom * ((128ULL * num) / denom));
  uint32_t p3 = denom;

  uint8_t p1_17_16 = static_cast<uint8_t>((p1 >> 16) & 0x03);
  uint8_t div4Bits = divBy4 ? 0x0C : 0x00;

  uint8_t params[8];
  params[0] = static_cast<uint8_t>((p3 >> 8) & 0xFF);
  params[1] = static_cast<uint8_t>(p3 & 0xFF);
  params[2] = div4Bits | p1_17_16;
  params[3] = static_cast<uint8_t>((p1 >> 8) & 0xFF);
  params[4] = static_cast<uint8_t>(p1 & 0xFF);
  params[5] = static_cast<uint8_t>(((p3 >> 16) & 0x0F) << 4 | ((p2 >> 16) & 0x0F));
  params[6] = static_cast<uint8_t>((p2 >> 8) & 0xFF);
  params[7] = static_cast<uint8_t>(p2 & 0xFF);

  if (i2cSpec && i2c_is_ready_dt(i2cSpec)) {
    i2c_burst_write_dt(i2cSpec, baseReg, params, 8);
  }
}
