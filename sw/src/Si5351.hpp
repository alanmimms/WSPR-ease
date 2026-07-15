#pragma once
#include <cstdint>
#include <cstddef>
#include <zephyr/drivers/i2c.h>

class Si5351 {
public:
  Si5351();
  ~Si5351();

  // Initializes the reference TCXO frequency, sets up PLL A (modulated) and PLL B (fixed 800MHz),
  // and configures CLK0 (6x carrier clock from PLL A) and CLK2 (fixed 40MHz PWM rate clock from PLL B).
  bool init(const struct i2c_dt_spec *spec, uint32_t tcxoFreqHz);

  // Sets the carrier base frequency. CLK0 will output exactly 6x this frequency.
  bool setCarrierFreq(uint32_t rfFreqHz);

  // Dynamically tunes CLK0 frequency offset via fractional PLLA modulation
  void tuneCarrierOffset(int32_t milliHzOffset);

  // Enable/disable the clock outputs (CLK0 and CLK2)
  void setClockOutputsEnabled(bool enabled);

  // Raw registers
  void writeRegister(uint8_t reg, uint8_t data);
  uint8_t readRegister(uint8_t reg);

private:
  void updateMultiSynthDividers(uint8_t clk);
  void setupPLLA(uint32_t targetPLLFreqHz, uint32_t refFreqHz);
  void setupPLLB(uint32_t targetPLLFreqHz, uint32_t refFreqHz);
  void writeSynthParams(uint8_t baseReg, uint32_t multOrDiv, uint32_t num, uint32_t denom, bool divBy4 = false);

  static constexpr uint8_t regOutputControl = 3;
  static constexpr uint8_t regCLKControlBase = 16;
  static constexpr uint8_t regPLLABase = 26;
  static constexpr uint8_t regPLLBBase = 34;
  static constexpr uint8_t regMultiSynthBase = 42;
  static constexpr uint8_t regPhaseOffsetBase = 165;
  static constexpr uint8_t regPLLReset = 177;
  static constexpr uint8_t regCrystalLoad = 183;
  static constexpr uint32_t max20BitValue = 1048575;

  const struct i2c_dt_spec *i2cSpec = nullptr;
  bool initialized = false;

  uint32_t tcxoFreqHz = 0;
  uint32_t pllaFreqHz = 0;
  uint32_t pllbFreqHz = 800000000; // Fixed 800 MHz PLL B for PWM clock

  uint32_t rfBaseFreqHz = 0;
  int32_t rfOffsetMilliHz = 0;

  uint32_t msInteger[8] = {0};
  uint32_t msNumerator[8] = {0};
  uint32_t msDenominator[8] = {0};
};
