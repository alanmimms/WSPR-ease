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
    .A(nco_out[31:16]), .B(16'd0),
    .C(m2h_d2), .D(16'd0),
    .O(phase_f),
    .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0)
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
  SB_MAC16 #( .A_REG(1'b1), .B_REG(1'b1), .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11) ) 
  m_r ( .CLK(clk90), .CE(1'b1), .A(ph_r_h), .B(16'd6), .O(d1),
        .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0) );

  SB_MAC16 #( .A_REG(1'b1), .B_REG(1'b1), .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11) ) 
  m_f ( .CLK(clk90), .CE(1'b1), .A(ph_f_h), .B(16'd6), .O(d2),
        .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0) );
  // Result at T=7

  // =====================================================================
  // 5. Hardcoded Power Enable (100% Duty Cycle)
  // =====================================================================
  // We have removed the DSP comparators entirely.
  // We hardcode the enable signals to 1'b1.
  
  wire en_r_raw = 1'b1;
  wire en_f_raw = 1'b1;
  
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
    oR <= decode(str_p3) & {4{txEnReg}}; // T=10
    oF <= decode(stf_p3) & {4{txEnReg}}; // T=10
    oRi <= oR; oFi <= oF;                // T=11
  end

  // Total latency: 11 cycles. Delay = 12.

  // PIN_TYPE 6'b010000 means "Output Pin, DDR output using dout_q_0 and dout_q_1"
  SB_IO #(.PIN_TYPE(6'b010000)) io0 (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[0]), .D_OUT_1(oFi[0]));
  SB_IO #(.PIN_TYPE(6'b010000)) io1 (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[1]), .D_OUT_1(oFi[1]));
  SB_IO #(.PIN_TYPE(6'b010000)) io2 (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[2]), .D_OUT_1(oFi[2]));
  SB_IO #(.PIN_TYPE(6'b010000)) io3 (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(oRi[3]), .D_OUT_1(oFi[3]));

endmodule
