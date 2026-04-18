/*
 * FPGA Control Module for WSPR-ease
 * Handles iCE40 FPGA communication via SPI
 */

#pragma once

#include <cstdint>

namespace wspr {

  // WSPR band definitions (dial frequencies in Hz)
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

    static const unsigned tcxoFreqHz = 40 * 1000 * 1000;
    static const uint64_t ncoHz = 90ull * 1000ull * 1000ull;

    int init();
    int reset();
    int loadBitstream(const char* path);

    // Frequency control
    int setFrequency(uint32_t freq_hz);
    uint32_t frequency() const { return currentFreq; }

    // Transmission control
    int startTX();
    int stopTX();
    bool isTransmitting() const { return initialized && transmitting; }

    // Power control (0-255)
    int setPowerLevel(uint8_t level);

    // Send WSPR symbol (0-3) - 4-FSK modulation
    int sendSymbol(uint8_t symbol);

    // LPF band switching
    int setLPFBand(WSPRBand band);
    WSPRBand getBand() const { return currentBand; }

    uint32_t getCounter();
    uint32_t getLiveCounter();

    // Raw register access for diagnostics
    int readRegister(uint8_t reg, uint32_t* value) { return spiReadReg(reg, value); }

    bool isInitialized() const { return initialized; }

  private:
    FPGA() = default;

    int spiWriteReg(uint8_t reg, uint32_t value);
    int spiReadReg(uint8_t reg, uint32_t* value);

    bool initialized = false;
    bool transmitting = false;
    uint32_t currentFreq = 0;
    WSPRBand currentBand = WSPRBand::Band20m;
  };

} // namespace wspr
