`timescale 1ns / 100ps
`default_nettype none

module Exciter (
    input  wire        clk90,
    input  wire        reset,
    input  wire [31:0] tuningWord,
    input  wire [7:0]  powerThreshold,
    input  wire        txEnable,

    output wire        rfPushBase,
    output wire        rfPushPeak,
    output wire        rfPullBase,
    output wire        rfPullPeak
);

  // =====================================================================
  // 1. Input Registration (T=1)
  // =====================================================================
  reg [31:0] twReg = 0;
  reg [7:0]  ptReg = 0;
  reg        txEnReg = 0, rst_nco = 1;

  always_ff @(posedge clk90) begin
    twReg   <= tuningWord;
    ptReg   <= powerThreshold;
    txEnReg <= txEnable;
    rst_nco <= reset;
  end

  // =====================================================================
  // 2. 32-bit Phase Accumulator (DSP 0)
  // =====================================================================
  wire [31:0] nco_out;
  SB_MAC16 #(
    .A_REG(1'b1), .B_REG(1'b1),
    .TOPADDSUB_LOWERINPUT(2'b00), .TOPADDSUB_UPPERINPUT(1'b0), // iA + iQ
    .BOTADDSUB_LOWERINPUT(2'b00), .BOTADDSUB_UPPERINPUT(1'b0), // iB + iS
    .MODE_8x8(1'b0),
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), // Carry LCO to HCI
    .TOPOUTPUT_SELECT(2'b01), .BOTOUTPUT_SELECT(2'b01) // Output registered adder (iQ, iS)
  ) dsp_nco (
    .CLK(clk90), .CE(1'b1),
    .C(16'd0), .D(16'd0),
    .A(twReg[31:16]), .B(twReg[15:0]),
    .IRSTTOP(rst_nco), .IRSTBOT(rst_nco), .ORSTTOP(rst_nco), .ORSTBOT(rst_nco),
    .O(nco_out)
  );
  // nco_out T=3

  // =====================================================================
  // 3. Falling Edge Phase Adder (DSP 1)
  // =====================================================================
  wire [31:0] phase_f;
  reg [15:0] m2h_d1, m2h_d2;
  always_ff @(posedge clk90) begin
    m2h_d1 <= {1'b0, twReg[31:17]};
    m2h_d2 <= m2h_d1; // T=3
  end

  SB_MAC16 #(
    .A_REG(1'b1), .C_REG(1'b1),
    .TOPADDSUB_LOWERINPUT(2'b00), .TOPADDSUB_UPPERINPUT(1'b1), // iA + iC
    .BOTADDSUB_LOWERINPUT(2'b00), .BOTADDSUB_UPPERINPUT(1'b1), // iB + iD
    .MODE_8x8(1'b0),
    .TOPOUTPUT_SELECT(2'b01), .BOTOUTPUT_SELECT(2'b01) // Output registered adder
  ) dsp_offset (
    .CLK(clk90), .CE(1'b1),
    .A(prh_d2), .B(16'd0), // Use prh_d2 (T=4) to align with m2h_d2 (T=4)
    .C(m2h_d2), .D(16'd0),
    .O(phase_f),
    .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0)
  );
  // phase_f T=5
  wire [15:0] ph_f_h = phase_f[31:16];

  // Align Rising Phase to T=5 (to match Falling Phase T=5)
  reg [15:0] prh_d1, prh_d2, prh_d3;
  always_ff @(posedge clk90) begin
    prh_d1 <= nco_out[31:16];
    prh_d2 <= prh_d1;
    prh_d3 <= prh_d2; // T=5
  end
  wire [15:0] ph_r_h = prh_d3;

  // =====================================================================
  // 4. Phase Dither Generator (LCG PRNG in DSP 4)
  // =====================================================================
  wire [31:0] lcg_out;
  
  // X_next = (X * 25173) + 13849 mod 2^16
  SB_MAC16 #(
    .A_REG(1'b1), .B_REG(1'b0), .C_REG(1'b0), .D_REG(1'b0),
    .TOPADDSUB_LOWERINPUT(2'b10), .TOPADDSUB_UPPERINPUT(1'b1), // Top: Mult_High + C
    .BOTADDSUB_LOWERINPUT(2'b10), .BOTADDSUB_UPPERINPUT(1'b1), // Bot: Mult_Low + D
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), // Propagate carry
    .TOPOUTPUT_SELECT(2'b01), .BOTOUTPUT_SELECT(2'b01)         // Unregistered adder output
  ) dsp_prng (
    .CLK(clk90), .CE(1'b1),
    .A(lcg_out[15:0]), .B(16'd25173), // Multiplier (a)
    .C(16'd0), .D(16'd13849),         // Increment (c)
    .O(lcg_out),
    .IRSTTOP(rst_nco), .IRSTBOT(rst_nco), .ORSTTOP(rst_nco), .ORSTBOT(rst_nco)
  );

  // The top 16 bits of the 32-bit PRNG result have the best entropy for a 16-bit LCG.
  // We use the full 16-bit width to ensure dither spans the entire fractional
  // remainder of the phase-to-state mapping.
  wire [15:0] noise_r = lcg_out[31:16];
  wire [15:0] noise_f = ~lcg_out[31:16]; 

  // =====================================================================
  // 5. Multipliers (DSP 2 & 3) with Zero-Cost Dither Injection
  // =====================================================================
  wire [31:0] d1, d2;

  // Rising Edge Mapping
  SB_MAC16 #( 
    .A_REG(1'b1), .B_REG(1'b1), .C_REG(1'b0), .D_REG(1'b0), 
    .TOPADDSUB_LOWERINPUT(2'b10), .TOPADDSUB_UPPERINPUT(1'b1), // Top: Mult_High + C
    .BOTADDSUB_LOWERINPUT(2'b10), .BOTADDSUB_UPPERINPUT(1'b1), // Bot: Mult_Low + D
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), // MUST propagate carry to State bits
    .TOPOUTPUT_SELECT(2'b01), .BOTOUTPUT_SELECT(2'b01) 
  ) m_r ( 
    .CLK(clk90), .CE(1'b1), 
    .A(ph_r_h), .B(16'd6), 
    .C(16'd0), .D(noise_r), // Small dither injected into fractional remainder
    .O(d1),
    .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0) 
  );

  // Falling Edge Mapping
  SB_MAC16 #( 
    .A_REG(1'b1), .B_REG(1'b1), .C_REG(1'b0), .D_REG(1'b0), 
    .TOPADDSUB_LOWERINPUT(2'b10), .TOPADDSUB_UPPERINPUT(1'b1), 
    .BOTADDSUB_LOWERINPUT(2'b10), .BOTADDSUB_UPPERINPUT(1'b1), 
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), 
    .TOPOUTPUT_SELECT(2'b01), .BOTOUTPUT_SELECT(2'b01) 
  ) m_f ( 
    .CLK(clk90), .CE(1'b1), 
    .A(ph_f_h), .B(16'd6), 
    .C(16'd0), .D(noise_f), // Small dither injected into fractional remainder
    .O(d2),
    .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0) 
  );
  // Result at T=8 (Multiplier reg T=7, Adder reg T=8)

  // =====================================================================
  // 6. Hardcoded Power Enable (100% Duty Cycle)
  // =====================================================================
  
  // Pipeline state to T=10
  reg [2:0] str_p1, str_p2;
  reg [2:0] stf_p1, stf_p2;
  always_ff @(posedge clk90) begin
    str_p1 <= d1[18:16]; str_p2 <= str_p1; // T=10
    stf_p1 <= d2[18:16]; stf_p2 <= stf_p1; // T=10
  end

  // Decode logic with modulo-6 protection to ensure dither-induced
  // overflows (e.g. State 5 + dither -> State 6) wrap back to State 0.
  function [3:0] decode(input [2:0] st);
    begin
      case (st)
        3'd0, 3'd6: decode = 4'b0001; 
        3'd1, 3'd7: decode = 4'b0010; 
        3'd2:       decode = 4'b0001;
        3'd3:       decode = 4'b0100; 
        3'd4:       decode = 4'b1000; 
        3'd5:       decode = 4'b0100;
        default:    decode = 4'b0000;
      endcase
    end
  endfunction

  reg [3:0] oR, oF;
  reg [3:0] oRi, oFi;
  always_ff @(posedge clk90) begin
    oR <= decode(str_p2) & {4{txEnReg}}; // T=11
    oF <= decode(stf_p2) & {4{txEnReg}}; // T=11
    oRi <= oR; oFi <= oF;                // T=12
  end

  // Total latency: 12 cycles.

  // PIN_TYPE 6'b010000 means "Output Pin, DDR output using dout_q_0 and dout_q_1"
  SB_IO #(.PIN_TYPE(6'b010000)) io0 (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[0]), .D_OUT_1(oFi[0]));
  SB_IO #(.PIN_TYPE(6'b010000)) io1 (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[1]), .D_OUT_1(oFi[1]));
  SB_IO #(.PIN_TYPE(6'b010000)) io2 (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[2]), .D_OUT_1(oFi[2]));
  SB_IO #(.PIN_TYPE(6'b010000)) io3 (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[3]), .D_OUT_1(oFi[3]));

endmodule
