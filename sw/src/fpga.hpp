/*
 * CPLD/FPGA Control Module for WSPR-ease
 * Handles CPLD interaction, Si5351 clock initialization, and SPI register updates.
 */

#pragma once

#include <cstdint>
#include "Si5351.hpp"

namespace wspr {

  enum class WSPRBand : uint32_t {
    Band160m = 1836600,
    Band80m  = 3568600,
    Band60m  = 5287200,
    Band40m  = 7038600,
    Band30m  = 10138700,
    Band20m  = 14095600,
    Band17m  = 18104600,
    Band15m  = 21094600,
    Band12m  = 24924600,
    Band10m  = 28124600,
    Band6m   = 50293000,
  };

  class FPGA {
  public:
    static FPGA& instance();

    static const unsigned tcxoFreqHz = 20 * 1000 * 1000; // 20 MHz TCXO reference for Si5351

    int init();
    int reset();
    int loadBitstream(const char* path);

    // Frequency control (CLK0 outputs 6x this frequency)
    int setFrequency(uint32_t freq_hz);
    uint32_t frequency() const { return currentFreq; }

    // Transmission control
    int startTX();
    int stopTX();
    bool isTransmitting() const { return initialized && transmitting; }

    // Send WSPR symbol (0-3) - 4-FSK modulation
    int sendSymbol(uint8_t symbol);

    // LPF band switching (controlled by loON, midON, hiON GPIOs)
    int setLPFBand(WSPRBand band);
    WSPRBand getBand() const { return currentBand; }

    // Atomic Polar Modulation update for AM, SSB, and CW envelope
    int updatePolarMod(uint16_t amplitude, uint16_t phase);

    // Soft reset trigger for internal CPLD state machines
    int triggerSoftReset(bool assertReset);

    // Raw register access for diagnostics
    int readRegister(uint8_t reg, uint32_t* value) { return spiReadReg(reg, value); }
    int writeRegister(uint8_t reg, uint32_t value) { return spiWriteReg(reg, value); }

    bool isInitialized() const { return initialized; }

    // Reference to Si5351 instance
    Si5351& getSi5351() { return si5351; }

  private:
    FPGA() = default;

    int spiWriteReg(uint8_t reg, uint32_t value);
    int spiReadReg(uint8_t reg, uint32_t* value);

    bool initialized = false;
    bool transmitting = false;
    uint32_t currentFreq = 0;
    WSPRBand currentBand = WSPRBand::Band20m;

    Si5351 si5351;
  };

} // namespace wspr
