# WSPR-ease FPGA: Phase Dithering & Zero-Cost DSP Noise Injection

## 1. The Problem: Phase Truncation Limit Cycles (The "Breathing" Bug)
During testing of the 1-2-1 modulated RF output (specifically
`rfPullPeak`), we observed a "breathing" effect where the pulse width
would vary, maintaining an incorrect width for several seconds at a
time before shifting.

This is not an electrical issue or a random logic glitch; we slammed
into a fundamental digital synthesis wall known as a **Phase
Truncation Limit Cycle**.

### The Math Behind the Breathing
At a 90MHz system clock, generating a 9MHz RF output gives us exactly
10 clock cycles per RF period. However, our 1-2-1 synthesis requires
dividing that period into **6 equal states** ($60^\circ$ wedges).
* $10 \div 6 = 1.666$ clock cycles per state.

Because the FPGA can only sample on clock edges, the states map out in
a Bresenham-style sequence of durations (e.g., `[2, 2, 1, 2, 2, 1]`
clock cycles).

Furthermore, our ideal 32-bit tuning word for 9MHz is `429,496,729.6`.
Because we truncate this to an integer (`0x19999999`), we introduce an
incredibly small phase error of ~0.6 counts per clock cycle. It takes
about **13.2 seconds** for this microscopic error to accumulate enough
to drift the grid mismatch by one full state.

Instead of the pulse widths averaging out cycle-by-cycle, the system
gets stuck in a "limit cycle," holding a slightly-too-wide or
slightly-too-narrow pulse for seconds at a time. This manifests as
low-frequency phase modulation (audio-rate jitter) on the RF output.

## 2. The Solution: Phase Dithering
To stop the 13-second breathing and force the pulse widths to average
out over a few cycles, we must break the limit cycle. We achieve this
through **Phase Dithering**.

By adding a fast, pseudo-random number to our phase accumulator just
before we slice it into the 6 states, we intentionally shake the phase
back and forth across the state boundaries.
* **The Result:** The slow, 13-second drift is shattered. The state
  boundaries jitter rapidly on a cycle-by-cycle basis.
* **The RF Impact:** This turns low-frequency phase drift into
  high-frequency broadband phase noise. Because it's pushed to high
  frequencies, our physical RF bandpass filters will effortlessly
  smooth it out into a perfectly clean, average RF wave.

## 3. Implementation: "Zero-Cost" DSP Injection
At 90MHz on the iCE40UP5K, soft-logic (like fabric-based LFSRs and
adders) will destroy our timing closure due to routing sprawl.
Instead, we implement the dither entirely within the hard DSP slices
(`SB_MAC16`), requiring **zero extra fabric logic and zero extra
latency**.

### Step A: The PRNG Generator (DSP 4)
We use a Linear Congruential Generator (LCG) to create pseudo-random
noise. The formula is $X_{n+1} = (X_n \cdot 25173 + 13849)
\pmod{2^{16}}$. This algorithm is entirely multiplication and
addition, making it a perfect fit for a standalone `SB_MAC16` block.

```systemverilog
  // =====================================================================
  // Phase Dither Generator (LCG PRNG in DSP)
  // =====================================================================
  wire [31:0] lcg_out;
  
  // X_next = (X * 25173) + 13849
  SB_MAC16 #(
    .A_REG(1'b1), .B_REG(1'b0), .C_REG(1'b0), .D_REG(1'b0),
    .TOPADDSUB_LOWERINPUT(2'b00), .TOPADDSUB_UPPERINPUT(1'b1), // Top add C
    .BOTADDSUB_LOWERINPUT(2'b00), .BOTADDSUB_UPPERINPUT(1'b1), // Bot add D
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), // Propagate carry
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11)         // Register output
  ) dsp_prng (
    .CLK(clk90), .CE(1'b1),
    .A(lcg_out[15:0]), .B(16'd25173), // Multiplier (a)
    .C(16'd0), .D(16'd13849),         // Increment (c)
    .O(lcg_out),
    .IRSTTOP(rst_nco), .IRSTBOT(rst_nco), .ORSTTOP(rst_nco), .ORSTBOT(rst_nco)
  );

  // The top 16 bits of an LCG have the best entropy.
  // Invert the falling edge noise to lightly decorrelate the DDR samples.
  wire [15:0] noise_r = lcg_out[31:16];
  wire [15:0] noise_f = ~lcg_out[31:16]; 
```

### Step B: Zero-Cost Injection into State Mapping
Currently, our phase-to-state mapping multiplies the upper 16 bits of
phase by 6 (e.g., `State = (Phase * 6) >> 16`).

The `SB_MAC16` block natively calculates $(A \times B) + \{C, D\}$. By
piping our `noise` signals directly into the `D` port (the lower 16
bits) of the existing multipliers (`m_r` and `m_f`), we get the dither
addition for free.

**Crucial Configuration:** We must set
`.TOPADDSUB_CARRYSELECT(2'b10)`. This ensures that when the injected
noise causes the fractional remainder to overflow, the carry bit
correctly propagates up and increments the actual State bits
(`d1[18:16]`).

```systemverilog
  // =====================================================================
  // Multipliers (DSP 2 & 3) with Zero-Cost Dither Injection
  // =====================================================================
  wire [31:0] d1, d2;

  // Rising Edge Mapping
  SB_MAC16 #( 
    .A_REG(1'b1), .B_REG(1'b1), .C_REG(1'b1), .D_REG(1'b1), 
    .TOPADDSUB_LOWERINPUT(2'b00), .TOPADDSUB_UPPERINPUT(1'b1), // Top: Mult_High + C
    .BOTADDSUB_LOWERINPUT(2'b00), .BOTADDSUB_UPPERINPUT(1'b1), // Bot: Mult_Low + D
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), // MUST propagate carry to State bits
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11) 
  ) m_r ( 
    .CLK(clk90), .CE(1'b1), 
    .A(ph_r_h), .B(16'd6), 
    .C(16'd0), .D(noise_r), // Dither injected into fractional remainder
    .O(d1),
    .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0) 
  );

  // Falling Edge Mapping
  SB_MAC16 #( 
    .A_REG(1'b1), .B_REG(1'b1), .C_REG(1'b1), .D_REG(1'b1), 
    .TOPADDSUB_LOWERINPUT(2'b00), .TOPADDSUB_UPPERINPUT(1'b1), 
    .BOTADDSUB_LOWERINPUT(2'b00), .BOTADDSUB_UPPERINPUT(1'b1), 
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), 
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11) 
  ) m_f ( 
    .CLK(clk90), .CE(1'b1), 
    .A(ph_f_h), .B(16'd6), 
    .C(16'd0), .D(noise_f), // Dither injected into fractional remainder
    .O(d2),
    .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0) 
  );
```

## Summary
By offloading the PRNG generation to the 4th DSP block and routing the
noise into the unused `D` ports of our existing multipliers, we
completely resolve the limit cycle bug. The 13-second phase breathing
is eliminated, transforming into easily filtered high-frequency
dither, all while preserving our strict 90MHz timing closure and
leaving fabric logic free.
