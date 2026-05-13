#pragma once

#include "../sw/hal/hal.hpp"
#include "VTop.h"
#include <cstdint>
#include <vector>

/**
 * @brief Centralized simulation timing control.
 */
class SimTime {
public:
  SimTime(uint64_t clockHz, VerilatedVcdC* tfp = nullptr) 
    : clockHz(clockHz), halfCycles(0), tfp(tfp) {
    psPerHalfCycle = 1000000000000.0 / (2.0 * (double)clockHz);
  }

  void step(VTop* top, int halfCyclesToStep = 1) {
    for (int i = 0; i < halfCyclesToStep; i++) {
      top->clk = !top->clk;
      top->eval();
      halfCycles++;
      if (tfp) tfp->dump(getTimePs());
    }
  }

  vluint64_t getTimePs() const {
    return (vluint64_t)((double)halfCycles * psPerHalfCycle);
  }

  vluint64_t getHalfCycles() const {
    return halfCycles;
  }

  uint64_t getClockHz() const { return clockHz; }

private:
  uint64_t clockHz;
  vluint64_t halfCycles;
  double psPerHalfCycle;
  VerilatedVcdC* tfp;
};

/**
 * @brief SPI HAL implementation for Verilator simulation.
 *
 * This implementation drives the SPI signals in the VTop module
 * to send commands and data to the FPGA.
 */
class SimSpi : public HAL::ISpi {
public:
  SimSpi(VTop* top, SimTime* timer)
    : top(top), timer(timer) {}

  void write(const uint8_t* data, size_t len) override {
    transceive(data, nullptr, len);
  }

  void transceive(const uint8_t* txData, uint8_t* rxData, size_t len) {
    // Assert CS
    top->fpgaNCS = 0;
    timer->step(top, 40); // Wait 20 full cycles for synchronizers

    for (size_t b = 0; b < len; b++) {
      uint8_t txByte = txData[b];
      uint8_t rxByte = 0;

      for (int i = 7; i >= 0; i--) {
        top->fpgaMOSI = (txByte >> i) & 1;
        timer->step(top, 2); // Small setup time
        
        // Rise SCLK
        top->fpgaSCLKpin = 1;
        timer->step(top, 20); // Wait 10 full cycles
        
        // Sample MISO on Rising Edge (Mode 0)
        rxByte = (rxByte << 1) | (top->fpgaMISO & 1);
        
        // Fall SCLK
        top->fpgaSCLKpin = 0;
        timer->step(top, 20); // Wait 10 full cycles
      }
      
      if (rxData) {
        rxData[b] = rxByte;
      }
    }

    // Deassert CS
    top->fpgaNCS = 1;
    timer->step(top, 40); // Wait 20 full cycles
  }

  // Helper for 5-byte register write
  void writeReg(uint8_t reg, uint32_t value) {
    uint8_t buf[5];
    buf[0] = 0x80 | (reg & 0x7F);
    buf[1] = (value >> 24) & 0xFF;
    buf[2] = (value >> 16) & 0xFF;
    buf[3] = (value >> 8) & 0xFF;
    buf[4] = value & 0xFF;
    write(buf, 5);
  }

  // Helper for 5-byte register read
  uint32_t readReg(uint8_t reg) {
    uint8_t tx[5] = { (uint8_t)(reg & 0x7F), 0, 0, 0, 0 };
    uint8_t rx[5] = { 0 };
    transceive(tx, rx, 5);
    return ((uint32_t)rx[1] << 24) | ((uint32_t)rx[2] << 16) | 
           ((uint32_t)rx[3] << 8) | (uint32_t)rx[4];
  }

private:
  VTop* top;
  SimTime* timer;
};

/**
 * @brief Timer HAL implementation for Verilator simulation.
 *
 * Tracks simulation time in picoseconds.
 */
class SimTimer : public HAL::ITimer {
public:
  SimTimer(SimTime* timer) : timer(timer) {}

  int64_t getUptimeMs() override {
    return timer->getTimePs() / 1000000000LL; // ps to ms
  }

  int64_t getUptimePs() override {
    return timer->getTimePs();
  }

  void sleepMs(int32_t ms) override {
    // No-op in simulation
  }

private:
  SimTime* timer;
};
