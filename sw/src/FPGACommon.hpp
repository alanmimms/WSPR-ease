#pragma once
#include <cstdint>

namespace wspr {

  // NCO system clock rate (90 MHz)
  constexpr uint64_t ncoHz = 90ULL * 1000ULL * 1000ULL;

  // WSPR tone spacing in Hz
  constexpr double wsprToneSpacingHz = 1.46484375;

  // Threshold frequency (10 MHz) to switch between 1-2-1 and Square Wave mode
  constexpr uint32_t modeSquareThresholdHz = 10000000;

  /**
   * @brief Calculates the NCO tuning word for a 48-bit accumulator.
   * 
   * @param freqHz Target frequency in Hz.
   * @param ncoHzVal NCO clock frequency in Hz.
   * @return uint64_t The 48-bit NCO tuning word.
   */
  inline uint64_t calculateNCOTuningWord(uint64_t freqHz, uint64_t ncoHzVal = ncoHz) {
    // Define constants for the 48-bit accumulator
    const uint64_t ncoShift = 48ULL;
    const uint64_t ncoScale = 1ULL << ncoShift;

    // Decompose ncoScale / ncoHzVal into quotient and remainder
    // ncoScale = (q * ncoHzVal) + r
    const uint64_t q = ncoScale / ncoHzVal;
    const uint64_t r = ncoScale % ncoHzVal;

    // result = (freqHz * q) + ((freqHz * r) / ncoHzVal)
    // Both intermediate products (freqHz * q) and (freqHz * r) 
    // fit within 64 bits for standard HF frequencies.
    uint64_t term1 = freqHz * q;
    uint64_t term2 = (freqHz * r) / ncoHzVal;

    uint64_t tuningWord = term1 + term2;

    return tuningWord;
  }

} // namespace wspr
