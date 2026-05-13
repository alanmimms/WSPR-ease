# WSPR-ease FPGA Simulation

This directory contains the Verilator-based testbench for the
WSPR-ease FPGA design, integrated with the ESP32 software stack.

**NOTE: Building this requires you to install `yosys-dev` or at least
have enough of yosys that the `yosys-config` program works and can
find the `--datdir` with its "ground truth" models for iCE40.**

**NOTE: Building this requires you use Verilator >= 5.x, which is
_not_ what is included in Ubuntu 24.04 and earlier. See
https://verilator.org/guide/latest/install.html#git-quick-install for
how to get this and build it if you need a newer version than your
Linux distribution provides (I did).**

## Architecture

The simulation integrates:

- **FPGA RTL** (`top.sv`, `sequencer.sv`) - The actual iCE40 FPGA
  design with NCO and 6-step sequencer
- **ESP32 Software** (`../sw/transmitter.cpp`,
  `../sw/wspr-encoder.cpp`) - Real transmitter and WSPR encoder code
- **Simulation HAL** (`simHAL.hpp`) - Hardware abstraction layer
  implementations for Verilator

This allows testing the complete signal chain from WSPR encoding
through SPI transactions to RF output generation.

## Building

```bash
make        # Build the simulation
make clean  # Clean build artifacts
```

## Running

```bash
make run              # Build and run simulation (with waveforms)
make fast             # Fast mode without waveforms
make superfast        # Event-driven fast-forward mode
make wave             # View waveforms with gtkwave (if installed)
```

**Direct execution:**
```bash
obj_dir/VTop                        # With waveforms
obj_dir/VTop --notrace              # No waveforms
obj_dir/VTop --notrace --fastforward # Superfast mode
```

**Performance Comparison:**

| Mode | Command | Time | VCD File | Use Case |
|------|---------|------|----------|----------|
| **Full** | `make run` | ~50s | 5.4GB | Detailed waveform analysis |
| **Fast** | `make fast` | ~7.6s | None | Functional verification |
| **Superfast** | `make superfast` | ~0.1s | None | Quick regression testing |

**How Superfast Works:**
- Event-driven simulation: skips clock cycles between SPI transactions
- Jumps directly to 10µs before each symbol transmission
- Only evaluates hardware during active periods
- ~76x faster than fast mode, ~500x faster than full simulation

## Simulation Output

The simulation runs for ~2 seconds of real-time transmission, sending
3 WSPR symbols:

- Symbol 1 at ~682 ms
- Symbol 2 at ~1365 ms
- Symbol 3 at ~2048 ms

Each symbol period is 682.67 ms (8192/12000 seconds), matching the
WSPR standard.

### Example Output

```
Starting reset...
Reset complete.
Transmitter started at 400000 ps
Running transmission simulation...
Symbol 1 sent at 682 ms
Symbol 2 sent at 1365 ms
Symbol 3 sent at 2048 ms

=== Simulation Summary ===
Simulation time: 2048 ms
Transmitter state: 1
SPI transactions: 3
RF activity cycles: 102399778

SUCCESS: Transmitted 3+ symbols via SPI to FPGA!
```

## Waveform Analysis

The generated `obj_dir/waveform.vcd` contains:

- **SPI transactions** - Tuning word updates every symbol period
- **RF outputs** - Push/Pull Base/Peak signals at 200 Msps
- **PLL and clock generation** - 40 MHz to 100 MHz conversion
- **NCO accumulator** - 32-bit phase accumulator operation

Fast mode is enabled by default, tracing only:
- First 10ms (startup)
- Periods when SPI is active
- Periods when RF outputs are active

This reduces VCD file size significantly.

## Modifying Test Parameters

Edit `tbTop.cpp` to change:

```cpp
const uint32_t dialFreqHz = 14097000;  // Frequency (Hz)
const char* callsign = "W1ABC";         // Callsign
const char* grid = "FN42";              // Maidenhead grid
const uint8_t powerDbm = 23;            // Power level

vluint64_t maxSimTime = 3000000000000LL; // Max sim time (ps)
bool fastMode = true;                    // Enable selective tracing
```

**Command-line options:**
- `--notrace` - Disable waveform generation (7x speedup)
- `--fastforward` - Enable event-driven fast-forward (76x speedup)

## Dependencies

- Verilator 5.x or later
- C++17 compatible compiler
- Optional: GTKWave for waveform viewing

## Integration with ESP32 Code

The sw/ directory code is compiled directly into the testbench. The
HAL interfaces (`HAL::ISpi`, `HAL::ITimer`) are implemented by:

- `SimSpi` - Drives FPGA SPI signals via Verilator interface
- `SimTimer` - Tracks simulation time in picoseconds

This ensures the same transmitter logic runs in both simulation and on
actual hardware.
