`timescale 1ns / 100ps
`default_nettype none

module Exciter (
    input  wire        clk90,
    input  wire        reset,
    input  wire [31:0] tuningWord,
    input  wire [7:0]  powerThreshold, // Ignored for timing test
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
  reg        txEnReg = 0, rstReg = 1;

  always_ff @(posedge clk90) begin
    twReg   <= tuningWord;
    txEnReg <= txEnable;
    rstReg  <= reset;
  end

  // =====================================================================
  // 2. 32-bit NCO in DSP Block (DSP 0)
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
    .A(16'd0), .B(16'd0), .AHOLD(1'b1), .BHOLD(1'b1), .CHOLD(1'b0), .DHOLD(1'b0),
    .IRSTTOP(rstReg), .IRSTBOT(rstReg), .ORSTTOP(rstReg), .ORSTBOT(rstReg),
    .OLOADTOP(1'b0), .OLOADBOT(1'b0), .ADDSUBTOP(1'b0), .ADDSUBBOT(1'b0),
    .OHOLDTOP(1'b0), .OHOLDBOT(1'b0), .CI(1'b0), .ACCUMCI(1'b0), .SIGNEXTIN(1'b0),
    .O(nco_out)
  );
  // nco_out coherent at T=3

  // =====================================================================
  // 3. Falling Edge Phase Adder (DSP 1)
  // =====================================================================
  wire [31:0] phase_f;
  reg [31:0] twH_d1 = 0, twH_d2 = 0;
  always_ff @(posedge clk90) begin
    twH_d1 <= {1'b0, twReg[31:1]};
    twH_d2 <= twH_d1;
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
    .C(twH_d2[31:16]), .D(twH_d2[15:0]),
    .O(phase_f),
    .IRSTTOP(rstReg), .IRSTBOT(rstReg), .ORSTTOP(1'b0), .ORSTBOT(1'b0)
  );
  // phase_f coherent at T=4

  // =====================================================================
  // 4. Multipliers (DSP 2 & 3)
  // =====================================================================
  wire [31:0] mul_r_out, mul_f_out;
  SB_MAC16 #( .A_REG(1'b1), .B_REG(1'b1), .TOPOUTPUT_SELECT(2'b00), .BOTOUTPUT_SELECT(2'b00) ) 
  dsp_mul_r ( .CLK(clk90), .CE(1'b1), .A(nco_out[31:16]), .B(16'd6), .O(mul_r_out) );

  SB_MAC16 #( .A_REG(1'b1), .B_REG(1'b1), .TOPOUTPUT_SELECT(2'b00), .BOTOUTPUT_SELECT(2'b00) ) 
  dsp_mul_f ( .CLK(clk90), .CE(1'b1), .A(phase_f[31:16]), .B(16'd6), .O(mul_f_out) );
  // mul_r_out T=5, mul_f_out T=6.

  reg [31:0] mul_r_s1 = 0, mul_r_s2 = 0;
  reg [31:0] mul_f_s1 = 0;
  always_ff @(posedge clk90) begin
    mul_r_s1 <= mul_r_out; mul_r_s2 <= mul_r_s1; // T=7
    mul_f_s1 <= mul_f_out; // T=7
  end

  // =====================================================================
  // 5. Control Pipeline (Comparison Eliminated)
  // =====================================================================
  wire [2:0] st_r   = mul_r_s2[18:16];
  wire [2:0] st_f   = mul_f_s1[18:16];

  reg [7:0] tx_p = 0;
  always_ff @(posedge clk90) begin
    tx_p <= {tx_p[6:0], txEnReg}; // Shift register for txEnable
  end
  // tx_p[6] is txEnable at T=8.

  reg en_r, en_f;
  reg [2:0] st_r_p, st_f_p;
  always_ff @(posedge clk90) begin
    en_r <= tx_p[6];
    en_f <= tx_p[6];
    st_r_p <= st_r; st_f_p <= st_f;
  end
  // Ready at T=9

  // =====================================================================
  // 6. Decoding & Output
  // =====================================================================
  function [3:0] decode_121(input [2:0] st);
    begin
      case (st)
        3'd0: decode_121 = 4'b0001;
        3'd1: decode_121 = 4'b0010;
        3'd2: decode_121 = 4'b0001;
        3'd3: decode_121 = 4'b0100;
        3'd4: decode_121 = 4'b1000;
        3'd5: decode_121 = 4'b0100;
        default: decode_121 = 4'b0000;
      endcase
    end
  endfunction

  reg [3:0] outR, outF;
  reg [3:0] outR_io, outF_io;
  always_ff @(posedge clk90) begin
    outR <= decode_121(st_r_p) & {4{en_r}}; // T=10
    outF <= decode_121(st_f_p) & {4{en_f}}; // T=10
    outR_io <= outR; outF_io <= outF; // T=11
  end

  // Final Latency: 11 cycles.

  SB_IO #(.PIN_TYPE(6'b011000)) ioPB (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR_io[0]), .D_OUT_1(outF_io[0]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioPP (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR_io[1]), .D_OUT_1(outF_io[1]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLB (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR_io[2]), .D_OUT_1(outF_io[2]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLP (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR_io[3]), .D_OUT_1(outF_io[3]));

endmodule
