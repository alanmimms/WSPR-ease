#include "Si5351.hpp"
#include <zephyr/sys/util.h>

Si5351::Si5351() {
  tcxoFreqHz = 0;
  currentBaseFreqHz = 0;
  pllFreqHz = MHZ(900); // 900 MHz high-resolution VCO benchmark target
  currentOffsetMilliHz = 0;
  staticAHCPhaseOffset = 0;

  ms0Integer = 0;
  ms0Numerator = 0;
  ms0Denominator = max20BitValue;

  ms1Integer = 0;
  ms1Numerator = 0;
  ms1Denominator = max20BitValue;
}

Si5351::~Si5351() {}

bool Si5351::init(uint32_t tcxoFreq, uint32_t baseFreqHz) {
  tcxoFreqHz = tcxoFreq;
  currentBaseFreqHz = baseFreqHz;

  // Configure master PLL-A to 900 MHz relative to reference input
  // tracking. Hardware baseline configuration setup execution...

  // XXX TODO

  // Establish baseline MultiSynth divider relationships
  setFreq(baseFreqHz);

  // Map balanced differential push-pull outputs for downstream stages
  // CLK0 & CLK1 route via MS0. CLK1 is 180 degrees out of phase
  // (0x1C).
  writeRegister(regClk0Control, 0x0C); 
  writeRegister(regClk1Control, 0x1C); 

  // CLK2 & CLK3 route via MS1. CLK3 is 180 degrees out of phase
  // (0x3C).
  writeRegister(regClk2Control, 0x2C); 
  writeRegister(regClk3Control, 0x3C); 

  // Reset master PLL once at initialization to coordinate startup
  // alignment
  writeRegister(regPllReset, 0xA0);

  // Release latch to clear clock line state
  setClockOutputsEnabled(true);

  return true;
}

bool Si5351::setFreq(uint32_t baseFreqHz) {
  currentBaseFreqHz = baseFreqHz;

  // Calculate baseline configurations for 1x fundamental spectrum
  ms0Integer = pllFreqHz / currentBaseFreqHz;
  uint64_t remainder0 = pllFreqHz % currentBaseFreqHz;
  ms0Numerator = (remainder0 * max20BitValue) / currentBaseFreqHz;

  // Calculate baseline configurations for 3x AHC tracking loop
  uint32_t AHCBlendedFreqHz = currentBaseFreqHz * 3;
  ms1Integer = pllFreqHz / AHCBlendedFreqHz;
  uint64_t remainder1 = pllFreqHz % AHCBlendedFreqHz;
  ms1Numerator = (remainder1 * max20BitValue) / AHCBlendedFreqHz;

  // Silence lines glitchlessly during multi-byte parameter changes
  setClockOutputsEnabled(false);
  updateMultiSynthDividers();
  setClockOutputsEnabled(true);

  return true;
}

void Si5351::tuneWSPROffset(int32_t milliHzOffset) {
  currentOffsetMilliHz = milliHzOffset;

  // Compute localized micro-adjustments for the fundamental fraction
  // numerator
  const auto mho = static_cast<int64_t>(milliHzOffset) * max20BitValue;
  const auto cbf = static_cast<int64_t>(currentBaseFreqHz) * 1000;
  int64_t scaledOffset0 = mho / cbf;
  uint32_t adjustedNumerator0 = ms0Numerator + scaledOffset0;

  // Compute scaled modifications to keep the 3x AHC loop in alignment
  int64_t scaledOffset1 = 3*mho / 3*cbf;
  uint32_t adjustedNumerator1 = ms1Numerator + scaledOffset1;

  setClockOutputsEnabled(false);

  // Update registers sequentially:
  // * Write adjustedNumerator0 elements to regMultiSynth0Base
  //   (Registers 44-49)
  // * Write adjustedNumerator1 elements to regMultiSynth1Base
  //   (Registers 52-57)
  // * Sequential multi-byte packout transfers execute over I2C here.

  setClockOutputsEnabled(true);
}

void Si5351::setAHCPhaseOffset(uint8_t phaseUnits) {
  staticAHCPhaseOffset = phaseUnits;

  setClockOutputsEnabled(false);

  // Assign quantization delay factor directly to MS1 phase offset
  // slot
  writeRegister(regMs1PhaseOffset, staticAHCPhaseOffset & 0x7F);

  // Execute local MultiSynth reset to lock phase shift without
  // disturbing PLL-A
  writeRegister(regPllReset, 0x60);

  setClockOutputsEnabled(true);
}

void Si5351::setClockOutputsEnabled(bool enabled) {
  // 0xF0 matches active states on lines CLK0-CLK3, 0xFF silences
  // completely
  writeRegister(regOutputControl, enabled ? 0xF0 : 0xFF);
}

void Si5351::writeRegister(uint8_t reg, uint8_t data) {
  // Native standalone I2C block transaction logic goes here
}

void Si5351::updateMultiSynthDividers() {
  // Unpacks and drives structured divider bytes straight to tracking
  // nodes
}
