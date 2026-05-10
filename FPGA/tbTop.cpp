#include "verilated.h"
#include "verilated_vcd_c.h"
#include "VTop.h"
#include "simHAL.hpp"
#include <iostream>
#include <cstdint>
#include <memory>
#include <iomanip>

/**
 * Calculates the NCO tuning word for a 48-bit accumulator.
 * Generalized for 32-bit architectures without 128-bit integer support.
 */
static uint64_t calculateNCOTuningWord(uint64_t freqHz, uint64_t ncoHz) {
  // Define constants for the 48-bit accumulator
  const uint64_t ncoShift = 48ULL;
  const uint64_t ncoScale = 1ULL << ncoShift;

  // Decompose ncoScale / ncoHz into quotient and remainder
  // ncoScale = (q * ncoHz) + r
  const uint64_t q = ncoScale / ncoHz;
  const uint64_t r = ncoScale % ncoHz;

  // result = (freqHz * q) + ((freqHz * r) / ncoHz)
  // Both intermediate products (freqHz * q) and (freqHz * r) 
  // fit within 64 bits for standard HF frequencies.
  uint64_t term1 = freqHz * q;
  uint64_t term2 = (freqHz * r) / ncoHz;

  uint64_t tuningWord = term1 + term2;

  return tuningWord;
}


int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  VTop* top = new VTop;

  bool enableTrace = true;
  for (int i = 1; i < argc; i++) {
    if (std::string(argv[i]) == "--notrace") {
      enableTrace = false;
    }
  }

  VerilatedVcdC* tfp = nullptr;
  if (enableTrace) {
    tfp = new VerilatedVcdC;
    Verilated::traceEverOn(true);
    top->trace(tfp, 99);
    tfp->open("waveform.vcd");
  }

  vluint64_t mainTime = 0;
  top->clk40 = 0;
  top->fpgaNCS = 1;
  top->fpgaSCLK_pin = 0;
  top->fpgaMOSI = 0;
  top->gnssPPS = 0;
  top->fpgaNRESET = 1;

  std::cout << "Starting simulation..." << std::endl;
  // Let PLL lock
  for (int i = 0; i < 100; i++) {
    top->clk40 = !top->clk40;
    top->eval();
    if (tfp) tfp->dump(mainTime);
    mainTime += 12500;
  }

  SimSpi spi(top, &mainTime);

  // Set Frequency: 5.555555 MHz
  uint64_t freqHz = 5555555ull;
  uint64_t ncoHz = 90ul*1000ull*1000ull;
  uint64_t tuningWord = calculateNCOTuningWord(freqHz, ncoHz);

  std::cout << "Setting Tuning Word: 0x" << std::hex << tuningWord << std::dec << " for " << freqHz << " Hz at 180 Msps" << std::endl;
  spi.writeReg(0x01, (uint32_t)(tuningWord & 0xFFFFFFFF));
  spi.writeReg(0x02, (uint32_t)(tuningWord >> 32));

  uint32_t rb_low = spi.readReg(0x01);
  uint32_t rb_high = spi.readReg(0x02);
  uint64_t rb = ((uint64_t)rb_high << 32) | rb_low;
  std::cout << "Readback Tuning: 0x" << std::hex << rb << std::dec << std::endl;

  uint32_t sig = spi.readReg(0x0F);
  std::cout << "Readback Signature: 0x" << std::hex << sig << std::dec << std::endl;

  std::cout << "Enabling TX (1-2-1 Mode)..." << std::endl;
  spi.writeReg(0x00, 0x00000001); // TX EN = 1, Mode Square = 0
  
  // Simulation loop
  std::cout << "Running RF simulation (1-2-1) for 5000 cycles..." << std::endl;
  for (int i = 0; i < 5000; i++) {
    top->clk40 = !top->clk40;
    top->eval();
    if (tfp) tfp->dump(mainTime);
    mainTime += 12500; // 40MHz clock half-period
  }

  std::cout << "Switching to Square Wave Mode..." << std::endl;
  spi.writeReg(0x00, 0x00000003); // TX EN = 1, Mode Square = 1

  std::cout << "Running RF simulation (Square Wave) for 5000 cycles..." << std::endl;
  for (int i = 0; i < 5000; i++) {
    top->clk40 = !top->clk40;
    top->eval();
    if (tfp) tfp->dump(mainTime);
    mainTime += 12500; // 40MHz clock half-period
  }

  top->final();
  if (tfp) { tfp->close(); delete tfp; }
  delete top;
  std::cout << "Simulation finished. Waveform saved to waveform.vcd" << std::endl;
  return 0;
}
