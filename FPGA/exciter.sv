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
  reg        txEnReg = 0, rstReg = 1;

  always_ff @(posedge clk90) begin
    twReg   <= tuningWord;
    ptReg   <= powerThreshold;
    txEnReg <= txEnable;
    rstReg  <= reset;
  end

  // =====================================================================
  // 2. 32-bit Phase Accumulator (DSP 0)
  // =====================================================================
  wire [31:0] nco_out;
  SB_MAC16 #(
    .C_REG(1'b1), .D_REG(1'b1),
    .TOPADDSUB_LOWERINPUT(2'b10), .TOPADDSUB_UPPERINPUT(1'b1),
    .BOTADDSUB_LOWERINPUT(2'b10), .BOTADDSUB_UPPERINPUT(1'b1),
    .MODE_8x8(1'b0),
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10),
    .TOPOUTPUT_SELECT(2'b10), .BOTOUTPUT_SELECT(2'b10)
  ) dsp_nco (
    .CLK(clk90), .CE(1'b1),
    .C(twReg[31:16]), .D(twReg[15:0]),
    .A(16'd0), .B(16'd0),
    .IRSTTOP(rstReg), .IRSTBOT(rstReg), .ORSTTOP(rstReg), .ORSTBOT(rstReg),
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
    .A_REG(1'b1), .B_REG(1'b1),
    .TOPADDSUB_LOWERINPUT(2'b01), .TOPADDSUB_UPPERINPUT(1'b1),
    .BOTADDSUB_LOWERINPUT(2'b01), .BOTADDSUB_UPPERINPUT(1'b1),
    .MODE_8x8(1'b0),
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11)
  ) dsp_offset (
    .CLK(clk90), .CE(1'b1),
    .A(nco_out[31:16]), .B(16'd1),
    .C(m2h_d2), .D(16'd0),
    .O(phase_f),
    .IRSTTOP(rstReg), .IRSTBOT(rstReg), .ORSTTOP(rstReg), .ORSTBOT(rstReg)
  );
  // phase_f T=5
  wire [15:0] ph_f_h = phase_f[31:16];

  // Align Rising Phase to T=5
  reg [15:0] prh_d1, prh_d2;
  always_ff @(posedge clk90) begin
    prh_d1 <= nco_out[31:16];
    prh_d2 <= prh_d1; // T=5
  end
  wire [15:0] ph_r_h = prh_d2;

  // =====================================================================
  // 4. Multipliers (DSP 2 & 3)
  // =====================================================================
  wire [31:0] d1, d2;
  SB_MAC16 #( .A_REG(1'b1), .B_REG(1'b1), .TOPOUTPUT_SELECT(2'b00), .BOTOUTPUT_SELECT(2'b00) ) 
  m_r ( .CLK(clk90), .CE(1'b1), .A(ph_r_h), .B(16'd6), .O(d1),
        .IRSTTOP(rstReg), .IRSTBOT(rstReg), .ORSTTOP(rstReg), .ORSTBOT(rstReg) );

  SB_MAC16 #( .A_REG(1'b1), .B_REG(1'b1), .TOPOUTPUT_SELECT(2'b00), .BOTOUTPUT_SELECT(2'b00) ) 
  m_f ( .CLK(clk90), .CE(1'b1), .A(ph_f_h), .B(16'd6), .O(d2),
        .IRSTTOP(rstReg), .IRSTBOT(rstReg), .ORSTTOP(rstReg), .ORSTBOT(rstReg) );
  // Result at T=7

  // =====================================================================
  // 5. DSP-Hardened Comparison (DSP 4 & 5)
  // =====================================================================
  // We use the DSP block to perform (Fractional Phase - Power Threshold).
  // If result is negative or zero, then Frac <= PT.
  // We care about the sign bit (O[31]) or O == 0.
  
  // Pipeline Power Threshold to T=7
  reg [7:0] pt_p [6:0];
  always_ff @(posedge clk90) begin
    pt_p[0] <= ptReg;
    for (int i=1; i<7; i++) pt_p[i] <= pt_p[i-1];
  end
  
  wire [31:0] cmp_r, cmp_f;
  // O = A*1 - C  => if O <= 0 then Frac <= PT
  SB_MAC16 #(
    .A_REG(1'b1), .B_REG(1'b1), .C_REG(1'b1),
    .TOPADDSUB_LOWERINPUT(2'b01), .TOPADDSUB_UPPERINPUT(1'b1),
    .BOTADDSUB_LOWERINPUT(2'b01), .BOTADDSUB_UPPERINPUT(1'b1),
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11)
  ) dsp_cmp_r (
    .CLK(clk90), .CE(1'b1),
    .A(d1[15:0]), .B(16'd1), // Multiplier is A*B
    .C({8'd0, pt_p[6]}), .D(16'd0),
    .ADDSUBTOP(1'b1), .ADDSUBBOT(1'b1), // Subtraction: A - C
    .O(cmp_r),
    .IRSTTOP(rstReg), .IRSTBOT(rstReg), .ORSTTOP(rstReg), .ORSTBOT(rstReg)
  );

  SB_MAC16 #(
    .A_REG(1'b1), .B_REG(1'b1), .C_REG(1'b1),
    .TOPADDSUB_LOWERINPUT(2'b01), .TOPADDSUB_UPPERINPUT(1'b1),
    .BOTADDSUB_LOWERINPUT(2'b01), .BOTADDSUB_UPPERINPUT(1'b1),
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11)
  ) dsp_cmp_f (
    .CLK(clk90), .CE(1'b1),
    .A(d2[15:0]), .B(16'd1),
    .C({8'd0, pt_p[6]}), .D(16'd0),
    .ADDSUBTOP(1'b1), .ADDSUBBOT(1'b1),
    .O(cmp_f),
    .IRSTTOP(rstReg), .IRSTBOT(rstReg), .ORSTTOP(rstReg), .ORSTBOT(rstReg)
  );
  // cmp result at T=9

  // =====================================================================
  // 6. Decoding & Output
  // =====================================================================
  // Sign bit check for comparison: result <= 0 means sign bit is 0 (for positive) 
  // Wait, in 2's complement: A - B <= 0  is  A <= B.
  // Sign bit O[31] = 1 means negative (A < B). O=0 means equal.
  // So (O[31] || O == 0) is A <= B.
  
  wire en_r_raw = (cmp_r[31] || cmp_r == 0);
  wire en_f_raw = (cmp_f[31] || cmp_f == 0);
  
  // Pipeline txEnable to T=9
  reg [8:0] tx_p = 0;
  always_ff @(posedge clk90) begin
    tx_p <= {tx_p[7:0], txEnReg};
  end
  
  // Pipeline state to T=9
  reg [2:0] str_p1, str_p2, str_p3;
  reg [2:0] stf_p1, stf_p2, stf_p3;
  always_ff @(posedge clk90) begin
    str_p1 <= d1[18:16]; str_p2 <= str_p1; str_p3 <= str_p2; // T=9
    stf_p1 <= d2[18:16]; stf_p2 <= stf_p1; stf_p3 <= stf_p2; // T=9
  end

  function [3:0] decode(input [2:0] st);
    begin
      case (st)
        3'd0: decode = 4'b0001; 3'd1: decode = 4'b0010; 3'd2: decode = 4'b0001;
        3'd3: decode = 4'b0100; 3'd4: decode = 4'b1000; 3'd5: decode = 4'b0100;
        default: decode = 4'b0000;
      endcase
    end
  endfunction

  reg [3:0] oR, oF;
  reg [3:0] oRi, oFi;
  always_ff @(posedge clk90) begin
    oR <= decode(str_p3) & {4{en_r_raw && tx_p[8]}}; // T=10
    oF <= decode(stf_p3) & {4{en_f_raw && tx_p[8]}}; // T=10
    oRi <= oR; oFi <= oF;                           // T=11
  end

  // Total latency: 11 cycles. Delay = 12.

  SB_IO #(.PIN_TYPE(6'b011000)) io0 (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[0]), .D_OUT_1(oFi[0]));
  SB_IO #(.PIN_TYPE(6'b011000)) io1 (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[1]), .D_OUT_1(oFi[1]));
  SB_IO #(.PIN_TYPE(6'b011000)) io2 (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[2]), .D_OUT_1(oFi[2]));
  SB_IO #(.PIN_TYPE(6'b011000)) io3 (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[3]), .D_OUT_1(oFi[3]));

endmodule
