# The Story So Far: Achieving 90 MHz 1-2-1 RF Synthesis on the iCE40UP5K

This document chronicles the architectural evolution and the brutal realities of timing closure required to build the WSPR-ease RF synthesizer.

## 1. The Goal: High-Purity RF via "1-2-1" Modulation
To transmit WSPR signals cleanly across the HF bands (up to the 10m band at ~28 MHz), a simple square-wave generator is insufficient. A square wave contains massive odd-order harmonics (the 3rd harmonic is only -9.5 dBc down), requiring bulky, band-specific analog low-pass filters.

To dramatically simplify the analog filtering, we adopted a **"1-2-1" stepped modulation scheme**. By driving a push-pull transformer with specific stepped amplitude states (`+1, +2, +1, -1, -2, -1`), the 3rd harmonic is theoretically eliminated.

**The Hardware Constraint:** To generate a ~30 MHz carrier with a 6-state sequence, we need an effective sampling rate of 180 Msps. On the Lattice iCE40UP5K, we achieve this by running the system clock at **90 MHz** and utilizing the Double Data Rate (DDR) `SB_IO` primitives to push two states per clock cycle.

## 2. The Naïve Implementation: Fabric is Too Slow
The initial RTL design was mathematically sound but physically naive. It relied heavily on the FPGA's general-purpose logic fabric (LUTs and routing):

*   **Phase Accumulator (NCO):** A 32-bit fabric adder `Phase = Phase + TuningWord`.
*   **Falling Edge Phase:** A second 32-bit fabric adder `Phase_F = Phase + (TuningWord / 2)`.
*   **Phase-to-State Mapping:** Fabric multiplication by 6 `(P * 6 = (P << 2) + (P << 1))`.
*   **Duty Cycle Control:** An 8-bit fabric comparison `(Frac <= PowerThreshold)`.

**The Result:** The design failed timing catastrophically, maxing out around **65-75 MHz**. The 11.1 ns timing budget for a 90 MHz clock was being consumed entirely by:
1.  **Long Carry Chains:** A 32-bit addition requires the carry signal to ripple through 32 consecutive LUTs.
2.  **Routing Delays:** In the iCE40 architecture, routing flight times often exceed logic delays. Moving data from the NCO, through the multiplier, to the comparator, and out to the pins meant crossing the chip multiple times.

## 3. The Pipelining Trap: Fighting the Architecture
To solve the fabric delays, we applied aggressive pipelining.

*   **Attempt 1 (16-bit stages):** Split the 32-bit NCO into two 16-bit stages. *Failed (~75 MHz).*
*   **Attempt 2 (8-bit stages):** Split the NCO into four 8-bit stages. *Failed (~80 MHz).*
*   **Attempt 3 (4-bit nibbles):** Split the NCO and the comparisons into ultra-fine 4-bit stages. *Failed (~87 MHz).*

**Why pipelining failed to provide margin:**
While pipelining shortens the physical carry chain within a single clock cycle, it forces the carry signal to exit the dedicated, high-speed `SB_CARRY` hardware, enter the general routing matrix, pass through a register, and re-enter the carry chain on the next cycle. This "entry/exit penalty" is roughly 2-3 ns. By breaking the chain into 8 nibbles, we traded carry delay for massive routing delay.

Furthermore, adding dozens of pipeline alignment registers consumed significant fabric area, forcing the place-and-route tool (`nextpnr`) to spread the logic further apart, increasing global routing delays. We were stuck at ~87-89 MHz, relying on "lucky" placement seeds (like `--seed 42` or `45`) to occasionally squeak by.

*Note on Clock Domain Crossing (CDC):* During this phase, `nextpnr` was also falsely failing timing on paths crossing from the slow SPI clock (12 MHz) to the 90 MHz domain. Because `nextpnr-ice40` lacks a `set_false_path` constraint, we solved this by double-registering the shadow registers and "faking" the SPI clock frequency to 0.1 MHz in `timing.py`, forcing the router to prioritize the 90 MHz internal logic.

## 4. The Breakthrough: 100% DSP-Hardened Phase Path
To achieve 90 MHz with robust, production-grade margin (meaning it compiles easily on any seed), we had to stop using the fabric for multi-bit arithmetic. The iCE40UP5K contains eight `SB_MAC16` DSP blocks. These hard macros are capable of operating at >200 MHz because the data never leaves the localized, high-speed silicon of the DSP tile.

We redesigned the entire critical path to reside within these DSP blocks:

1.  **DSP 0 (The NCO):** Configured the `SB_MAC16` in 32-bit Accumulator mode. This executes the `Phase = Phase + TuningWord` integration in a single cycle, completely eliminating the fabric carry chain.
2.  **DSP 1 (Falling Edge Offset):** Used a second DSP block as a 16-bit adder to calculate the mid-cycle phase `P_F = P_R + (M/2)` without fabric routing.
3.  **DSP 2 & DSP 3 (State Multipliers):** Handled the `Phase * 6` mapping for both the rising and falling edges, converting the top 16 bits of phase into the 0-5 state sequence.
4.  **DSP 4 & DSP 5 (Hardened Comparison):** The final fabric bottleneck was the 8-bit duty cycle comparator. We eliminated it by configuring two more `SB_MAC16` blocks to perform subtraction: `O = A - C`. By feeding the fractional phase into `A` and the `PowerThreshold` into `C`, the comparison `Frac <= PT` is evaluated instantly in hard logic by checking the resulting sign bit (`O[31] == 1` means `A < C`) or equality (`O == 0`).

## 5. Final Results
By moving the arithmetic and comparison entirely into the DSP column and only using the fabric for simple 1-bit logic and shift registers, the design transformed.

*   **Timing Closure:** The maximum frequency skyrocketed to **121.92 MHz**. This provides a massive **35.4% timing margin** over our 90 MHz target. The design is now "rock solid" and compiles reliably without seed tweaking.
*   **Signal Quality:** Co-simulation of the RTL confirms the 1-2-1 modulation performs flawlessly. At a 14.097 MHz WSPR test frequency, the 3rd harmonic is suppressed to **-54.9 dBc**, exactly matching our theoretical models and practically eliminating the need for aggressive analog filtering.
*   **Latency:** The highly-pipelined, DSP-centric architecture introduced an 11-cycle latency from the tuning word to the physical DDR pins, which is negligible for WSPR transmission.

The synthesizer is now production-ready.
