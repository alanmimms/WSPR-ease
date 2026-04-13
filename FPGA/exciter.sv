`timescale 1ns / 100ps
`default_nettype none

module Exciter (
		input  wire        clk90,
		input  wire        reset,
		input  wire [31:0] tuningWord,     // M
		input  wire [7:0]  powerThreshold,
		input  wire        txEnable,

		output wire        rfPushBase,
		output wire        rfPushPeak,
		output wire        rfPullBase,
		output wire        rfPullPeak
		);

  // =====================================================================
  // 1. Local Synchronization & Routing Firewalls
  // =====================================================================
  reg rst_l, txEn_l;
  reg [7:0] pt_s1, pt_pipe; 
  
  reg [31:0] tw_in, hw_in;

  always_ff @(posedge clk90) begin
    rst_l   <= reset;
    txEn_l  <= txEnable;
    
    pt_s1   <= powerThreshold;
    pt_pipe <= pt_s1; 
    
    tw_in   <= tuningWord;
    hw_in   <= {1'b0, tuningWord[31:1]};
  end

  // =====================================================================
  // 2. Nibble-Pipelined Tuning Words
  // =====================================================================
  reg [3:0] w_pipe [7:0][7:0];   
  reg [3:0] hw_pipe[7:0][8:0];   

  always_ff @(posedge clk90) begin
    for (int s = 0; s < 8; s = s + 1) begin
      w_pipe[s][0]  <= tw_in[s*4 +: 4];
      hw_pipe[s][0] <= hw_in[s*4 +: 4];
      
      for (int d = 1; d <= s; d = d + 1) begin
        w_pipe[s][d] <= w_pipe[s][d-1];
      end
      for (int d = 1; d <= s + 1; d = d + 1) begin
        hw_pipe[s][d] <= hw_pipe[s][d-1];
      end
    end
  end

  // =====================================================================
  // 3. Parallel 4-bit Segmented Accumulators
  // =====================================================================
  reg [3:0] acc[7:0];
  reg       c[7:0];

  reg [3:0] acc_f[7:0];
  reg       c_f[7:0];

  always_ff @(posedge clk90) begin
    if (rst_l) begin
      for (int s = 0; s < 8; s = s + 1) begin
        acc[s] <= 4'd0; c[s] <= 1'b0;
        acc_f[s] <= 4'd0; c_f[s] <= 1'b0;
      end
    end else begin
      {c[0], acc[0]} <= acc[0] + w_pipe[0][0];
      for (int s = 1; s < 8; s = s + 1) begin
        {c[s], acc[s]} <= acc[s] + w_pipe[s][s] + c[s-1];
      end

      {c_f[0], acc_f[0]} <= acc[0] + hw_pipe[0][1];
      for (int s = 1; s < 8; s = s + 1) begin
        {c_f[s], acc_f[s]} <= acc[s] + hw_pipe[s][s+1] + c_f[s-1];
      end
    end
  end

  // =====================================================================
  // 4. Deskewing / Alignment & Phase Registration
  // =====================================================================
  reg [3:0] acc_d1[7:0];
  always_ff @(posedge clk90) begin
    for (int s = 0; s < 8; s = s + 1) acc_d1[s] <= acc[s];
  end

  reg [3:0] deskew_r[7:0][7:0];
  reg [3:0] deskew_f[7:0][7:0];

  always_ff @(posedge clk90) begin
    for (int s = 0; s < 8; s = s + 1) begin
      deskew_r[s][0] <= acc_d1[s];
      deskew_f[s][0] <= acc_f[s];
      for (int d = 1; d <= 7 - s; d = d + 1) begin
        deskew_r[s][d] <= deskew_r[s][d-1];
        deskew_f[s][d] <= deskew_f[s][d-1];
      end
    end
  end

  reg [31:0] ph_rise, ph_fall;
  always_ff @(posedge clk90) begin
    ph_rise <= {deskew_r[7][0], deskew_r[6][1], deskew_r[5][2], deskew_r[4][3],
                deskew_r[3][4], deskew_r[2][5], deskew_r[1][6], deskew_r[0][7]};
    ph_fall <= {deskew_f[7][0], deskew_f[6][1], deskew_f[5][2], deskew_f[4][3],
                deskew_f[3][4], deskew_f[2][5], deskew_f[1][6], deskew_f[0][7]};
  end

  // =====================================================================
  // 5. Fast Native Subtraction Duty Cycle Check (4 cycles)
  // =====================================================================
  // Using native subtraction forces Yosys to map directly to independent SB_CARRY 
  // chains without attempting to share LUT logic. If A < B, a borrow is generated.
  
  wire [4:0] sub_lo_r = {1'b0, ph_rise[27:24]} - {1'b0, pt_pipe[3:0]};
  wire [4:0] sub_lo_f = {1'b0, ph_fall[27:24]} - {1'b0, pt_pipe[3:0]};

  reg b_lo_r, b_lo_f; // Borrow flags
  reg [3:0] hi_a_r, hi_b_r, hi_a_f, hi_b_f;
  reg txEn_c1;

  always_ff @(posedge clk90) begin
    b_lo_r <= sub_lo_r[4];
    b_lo_f <= sub_lo_f[4];
    
    hi_a_r <= ph_rise[31:28];
    hi_b_r <= pt_pipe[7:4];
    hi_a_f <= ph_fall[31:28];
    hi_b_f <= pt_pipe[7:4];
    txEn_c1 <= txEn_l;
  end

  // Subtract the upper nibbles, and also subtract the borrow from the lower nibble
  wire [4:0] sub_hi_r = {1'b0, hi_a_r} - {1'b0, hi_b_r} - {4'b0000, b_lo_r};
  wire [4:0] sub_hi_f = {1'b0, hi_a_f} - {1'b0, hi_b_f} - {4'b0000, b_lo_f};

  reg b_hi_r, b_hi_f;
  reg txEn_c2;

  always_ff @(posedge clk90) begin
    b_hi_r <= sub_hi_r[4];
    b_hi_f <= sub_hi_f[4];
    txEn_c2 <= txEn_c1;
  end

  reg en_c3_r, en_c3_f;
  reg en_c4_r, en_c4_f;

  always_ff @(posedge clk90) begin
    // If there IS a borrow out of the final MSB, it means Phase < Threshold
    en_c3_r <= txEn_c2 && b_hi_r;
    en_c3_f <= txEn_c2 && b_hi_f;

    en_c4_r <= en_c3_r;
    en_c4_f <= en_c3_f;
  end

  // =====================================================================
  // 6. 4-bit Pipelined Phase Multiplier (4 cycles)
  // =====================================================================
  wire [15:0] mul_a_r = {ph_rise[31:18], 2'b00};
  wire [15:0] mul_b_r = {1'b0, ph_rise[31:18], 1'b0};
  
  wire [15:0] mul_a_f = {ph_fall[31:18], 2'b00};
  wire [15:0] mul_b_f = {1'b0, ph_fall[31:18], 1'b0};

  reg mul_c0_r, mul_c0_f;
  reg [3:0] dmy0_r, dmy0_f;
  reg [11:0] a_d1_r, b_d1_r, a_d1_f, b_d1_f;
  always_ff @(posedge clk90) begin
    {mul_c0_r, dmy0_r} <= mul_a_r[3:0] + mul_b_r[3:0];
    {mul_c0_f, dmy0_f} <= mul_a_f[3:0] + mul_b_f[3:0];
    a_d1_r <= mul_a_r[15:4]; b_d1_r <= mul_b_r[15:4];
    a_d1_f <= mul_a_f[15:4]; b_d1_f <= mul_b_f[15:4];
  end

  reg mul_c1_r, mul_c1_f;
  reg [3:0] dmy1_r, dmy1_f;
  reg [7:0] a_d2_r, b_d2_r, a_d2_f, b_d2_f;
  always_ff @(posedge clk90) begin
    {mul_c1_r, dmy1_r} <= a_d1_r[3:0] + b_d1_r[3:0] + mul_c0_r;
    {mul_c1_f, dmy1_f} <= a_d1_f[3:0] + b_d1_f[3:0] + mul_c0_f;
    a_d2_r <= a_d1_r[11:4]; b_d2_r <= b_d1_r[11:4];
    a_d2_f <= a_d1_f[11:4]; b_d2_f <= b_d1_f[11:4];
  end

  reg mul_c2_r, mul_c2_f;
  reg [3:0] dmy2_r, dmy2_f;
  reg [3:0] a_d3_r, b_d3_r, a_d3_f, b_d3_f;
  always_ff @(posedge clk90) begin
    {mul_c2_r, dmy2_r} <= a_d2_r[3:0] + b_d2_r[3:0] + mul_c1_r;
    {mul_c2_f, dmy2_f} <= a_d2_f[3:0] + b_d2_f[3:0] + mul_c1_f;
    a_d3_r <= a_d2_r[7:4]; b_d3_r <= b_d2_r[7:4];
    a_d3_f <= a_d2_f[7:4]; b_d3_f <= b_d2_f[7:4];
  end

  reg [4:0] sum3_r, sum3_f; 
  always_ff @(posedge clk90) begin
    sum3_r <= a_d3_r + b_d3_r + mul_c2_r;
    sum3_f <= a_d3_f + b_d3_f + mul_c2_f;
  end

  wire [2:0] state_r = sum3_r[4:2]; 
  wire [2:0] state_f = sum3_f[4:2];

  // =====================================================================
  // 7. 1-2-1 Gate Decoding
  // =====================================================================
  function [3:0] decode_121(input [2:0] state, input en);
    logic [3:0] g;
    begin
      case (state)
        3'd0: g = 4'b0001; // +1
        3'd1: g = 4'b0011; // +2
        3'd2: g = 4'b0001; // +1
        3'd3: g = 4'b0100; // -1
        3'd4: g = 4'b1100; // -2
        3'd5: g = 4'b0100; // -1
        default: g = 4'b0000;
      endcase
      decode_121 = g & {4{en}};
    end
  endfunction

  reg [3:0] outR, outF;
  always_ff @(posedge clk90) begin
    outR <= decode_121(state_r, en_c4_r);
    outF <= decode_121(state_f, en_c4_f);
  end

  // =====================================================================
  // 8. I/O Pin DDR Output
  // =====================================================================
  SB_IO #(.PIN_TYPE(6'b011000)) ioPB (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR[0]), .D_OUT_1(outF[0]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioPP (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR[1]), .D_OUT_1(outF[1]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLB (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR[2]), .D_OUT_1(outF[2]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLP (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR[3]), .D_OUT_1(outF[3]));

endmodule // Exciter
