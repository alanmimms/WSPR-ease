#pragma once
#include <cstdint>

class Si5351 {
public:
  Si5351();
  ~Si5351();

  // Initializes reference parameters, sets the fixed master PLL-A, 
  // and configures CLK0/CLK1 (1x TX differential pair) and 
  // CLK2/CLK3 (3x AHC differential pair).
  bool init(uint32_t tcxoFreqHz, uint32_t baseFreqHz);

  // Handles band switching by shifting the operating base
  // frequencies. Disables outputs glitchlessly, updates MultiSynth
  // dividers, and restores clock state.
  bool setFreq(uint32_t baseFreqHz);

  // Shifts both the 1x TX and 3x AHC numerators concurrently using
  // millihertz metrics. Modulates frequency relative to the
  // established base parameters.
  void tuneWSPROffset(int32_t milliHzOffset);

  // Establishes a static phase offset for the 3x AHC clock during
  // testing. Increments represent 1/4 of a VCO cycle (~278 ps at 900
  // MHz VCO).
  void setAHCPhaseOffset(uint8_t phaseUnits);

private:
  void writeRegister(uint8_t reg, uint8_t data);
  void updateMultiSynthDividers();
  void setClockOutputsEnabled(bool enabled);

  // Encapsulated explicit internal register maps
  static constexpr uint8_t regOutputControl = 3;
  static constexpr uint8_t regClk0Control = 16;
  static constexpr uint8_t regClk1Control = 17;
  static constexpr uint8_t regClk2Control = 18;
  static constexpr uint8_t regClk3Control = 19;
  static constexpr uint8_t regMultiSynth0Base = 42;
  static constexpr uint8_t regMultiSynth1Base = 50;
  static constexpr uint8_t regMs1PhaseOffset = 166;
  static constexpr uint8_t regPllReset = 177;
  static constexpr uint32_t max20BitValue = 1048575;

  uint32_t tcxoFreqHz;
  uint32_t currentBaseFreqHz;
  uint32_t pllFreqHz;
  int32_t currentOffsetMilliHz;
  uint8_t staticAHCPhaseOffset;

  // Encapsulated configuration fields for the 1x TX MultiSynth (MS0)
  uint32_t ms0Integer;
  uint32_t ms0Numerator;
  uint32_t ms0Denominator;

  // Encapsulated configuration fields for the 3x AHC MultiSynth (MS1)
  uint32_t ms1Integer;
  uint32_t ms1Numerator;
  uint32_t ms1Denominator;
};
