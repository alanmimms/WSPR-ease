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
  // 2. 32-bit Phase Accumulator (8-stage 4-bit Pipelined)
  // =====================================================================
  // This ensures maximum frequency by keeping carry chains extremely short.
  
  reg [3:0] acc [7:0];
  reg       c   [7:0];
  reg [3:0] tw_p [7:0][7:0]; // tw_p[nibble][delay]

  always_ff @(posedge clk90) begin
    for (int i=0; i<8; i++) begin
        tw_p[i][0] <= twReg[i*4 +: 4];
        for (int j=1; j<=i; j++) tw_p[i][j] <= tw_p[i][j-1];
    end

    // Stage 0
    if (rstReg) {c[0], acc[0]} <= 0;
    else        {c[0], acc[0]} <= acc[0] + tw_p[0][0];

    // Stages 1-7
    for (int i=1; i<8; i++) begin
        {c[i], acc[i]} <= acc[i] + tw_p[i][i] + c[i-1];
    end
  end

  // Latencies: acc[i] is coherent at T=i+2? 
  // acc[0] updated at T=2.
  // acc[1] updated at T=3.
  // acc[7] updated at T=9.
  
  // Align for coherent 32-bit word at T=10
  reg [3:0] acc_sync [7:0];
  always_ff @(posedge clk90) begin
    acc_sync[7] <= acc[7];
    for (int i=0; i<7; i++) begin
        reg [3:0] pipe [7:0];
        pipe[0] <= acc[i];
        for (int j=1; j< (7-i); j++) pipe[j] <= pipe[j-1];
        acc_sync[i] <= pipe[7-i-1];
    end
  end
  // Correction: manually unroll for safety
  reg [3:0] a0_p[6:0], a1_p[5:0], a2_p[4:0], a3_p[3:0], a4_p[2:0], a5_p[1:0], a6_p[0:0];
  always_ff @(posedge clk90) begin
    a0_p[0] <= acc[0]; for(int i=1;i<7;i++) a0_p[i] <= a0_p[i-1];
    a1_p[0] <= acc[1]; for(int i=1;i<6;i++) a1_p[i] <= a1_p[i-1];
    a2_p[0] <= acc[2]; for(int i=1;i<5;i++) a2_p[i] <= a2_p[i-1];
    a3_p[0] <= acc[3]; for(int i=1;i<4;i++) a3_p[i] <= a3_p[i-1];
    a4_p[0] <= acc[4]; for(int i=1;i<3;i++) a4_p[i] <= a4_p[i-1];
    a5_p[0] <= acc[5]; for(int i=1;i<2;i++) a5_p[i] <= a5_p[i-1];
    a6_p[0] <= acc[6];
  end
  
  wire [15:0] ph_r_h = {acc[7], a6_p[0], a5_p[1], a4_p[2]}; // T=10
  
  // =====================================================================
  // 3. Falling Edge Phase (Simplified for high speed)
  // =====================================================================
  // Top 16 bits of P_f = Top 16 bits of P_r + Top 16 bits of (M/2)
  // Just use a single registered 16-bit add on the already coherent P_r_h.
  reg [15:0] m2_h_pipe [9:0];
  always_ff @(posedge clk90) begin
    m2_h_pipe[0] <= twReg[31:17];
    for (int i=1; i<10; i++) m2_h_pipe[i] <= m2_h_pipe[i-1];
  end
  
  reg [15:0] ph_f_h = 0;
  always_ff @(posedge clk90) ph_f_h <= ph_r_h + m2_h_pipe[8]; // T=11
  
  reg [15:0] ph_r_h_final = 0;
  always_ff @(posedge clk90) ph_r_h_final <= ph_r_h; // T=11

  // =====================================================================
  // 4. Multipliers (DSP)
  // =====================================================================
  wire [31:0] dsp1_out, dsp2_out;
  SB_MAC16 #( .A_REG(1'b1), .B_REG(1'b1), .TOPOUTPUT_SELECT(2'b00), .BOTOUTPUT_SELECT(2'b00) ) 
  dsp_mul_r ( .CLK(clk90), .CE(1'b1), .A(ph_r_h_final), .B(16'd6), .O(dsp1_out) );

  SB_MAC16 #( .A_REG(1'b1), .B_REG(1'b1), .TOPOUTPUT_SELECT(2'b00), .BOTOUTPUT_SELECT(2'b00) ) 
  dsp_mul_f ( .CLK(clk90), .CE(1'b1), .A(ph_f_h), .B(16'd6), .O(dsp2_out) );

  wire [2:0] state_r = dsp1_out[18:16];
  wire [2:0] state_f = dsp2_out[18:16];
  wire [7:0] frac_r  = dsp1_out[15:8];
  wire [7:0] frac_f  = dsp2_out[15:8];

  // =====================================================================
  // 5. Comparison Pipeline (Nibble-wise)
  // =====================================================================
  // DSP out at T=13. ptReg at T=1.
  reg [7:0] pt_p [13:0];
  reg       tx_p [13:0];
  always_ff @(posedge clk90) begin
    pt_p[0] <= ptReg; tx_p[0] <= txEnReg;
    for (int i=1; i<14; i++) begin pt_p[i] <= pt_p[i-1]; tx_p[i] <= tx_p[i-1]; end
  end

  reg cmp_lo_r, cmp_hi_r, cmp_lo_f, cmp_hi_f;
  always_ff @(posedge clk90) begin
    cmp_lo_r <= (frac_r[3:0] <= pt_p[12][3:0]);
    cmp_hi_r <= (frac_r[7:4] <  pt_p[12][7:4]);
    cmp_lo_f <= (frac_f[3:0] <= pt_p[12][3:0]);
    cmp_hi_f <= (frac_f[7:4] <  pt_p[12][7:4]);
  end
  
  reg en_r, en_f;
  reg [2:0] st_r_p, st_f_p;
  reg [2:0] st_r_p_d1, st_f_p_d1;
  always_ff @(posedge clk90) begin
    en_r <= tx_p[13] && (cmp_hi_r || (frac_r[7:4] == pt_p[13][7:4] && cmp_lo_r));
    en_f <= tx_p[13] && (cmp_hi_f || (frac_f[7:4] == pt_p[13][7:4] && cmp_lo_f));
    st_r_p <= state_r; st_f_p <= state_f;
    st_r_p_d1 <= st_r_p; st_f_p_d1 <= st_f_p;
  end

  // =====================================================================
  // 6. Decoding
  // =====================================================================
  function [3:0] decode_121(input [2:0] st);
    begin
      case (st)
        3'd0: decode_121 = 4'b0001; // +1
        3'd1: decode_121 = 4'b0010; // +2
        3'd2: decode_121 = 4'b0001; // +1
        3'd3: decode_121 = 4'b0100; // -1
        3'd4: decode_121 = 4'b1000; // -2
        3'd5: decode_121 = 4'b0100; // -1
        default: decode_121 = 4'b0000;
      endcase
    end
  endfunction

  reg [3:0] outR, outF;
  reg [3:0] outR_d1, outF_d1;
  reg [3:0] outR_d2, outF_d2;
  always_ff @(posedge clk90) begin
    outR <= decode_121(st_r_p_d1) & {4{en_r}};
    outF <= decode_121(st_f_p_d1) & {4{en_f}};
    
    // Extra pipeline stages to bridge routing to IO
    outR_d1 <= outR; outR_d2 <= outR_d1;
    outF_d1 <= outF; outF_d2 <= outF_d1;
  end

  SB_IO #(.PIN_TYPE(6'b011000)) ioPB (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR_d2[0]), .D_OUT_1(outF_d2[0]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioPP (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR_d2[1]), .D_OUT_1(outF_d2[1]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLB (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR_d2[2]), .D_OUT_1(outF_d2[2]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLP (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR_d2[3]), .D_OUT_1(outF_d2[3]));

endmodule
