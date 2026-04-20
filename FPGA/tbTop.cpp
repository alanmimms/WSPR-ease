#include "verilated.h"
#include "verilated_vcd_c.h"
#include "VTop.h"
#include "simHAL.hpp"
#include <iostream>
#include <cstdint>
#include <memory>
#include <iomanip>

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
  uint32_t freqHz = 5555555;
  // Update rate in simulation:
  // clk40 is passed through to clk90 (90MHz).
  // DDR means 2 updates per clk90 cycle = 180Msps.
  uint32_t tuningWord = ((uint64_t)freqHz << 32) / 180000000ULL;

  std::cout << "Setting Tuning Word: 0x" << std::hex << tuningWord << std::dec << " for " << freqHz << " Hz at 180 Msps" << std::endl;
  spi.writeReg(0x01, tuningWord);

  uint32_t rb = spi.readReg(0x01);
  std::cout << "Readback Tuning: 0x" << std::hex << rb << std::dec << std::endl;

  std::cout << "Enabling TX..." << std::endl;
  spi.writeReg(0x00, 0xFF000001); // TX EN = 1, Power Threshold = 255
  
  // Simulation loop
  std::cout << "Running RF simulation for 5000 cycles..." << std::endl;
  for (int i = 0; i < 10000; i++) {
    top->clk40 = !top->clk40;
    top->eval();
    if (tfp) tfp->dump(mainTime);
    mainTime += 5555; // 90MHz clock half-period
  }

  top->final();
  if (tfp) { tfp->close(); delete tfp; }
  delete top;
  std::cout << "Simulation finished. Waveform saved to waveform.vcd" << std::endl;
  return 0;
}
