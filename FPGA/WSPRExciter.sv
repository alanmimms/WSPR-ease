`timescale 1ns / 100ps
`default_nettype none

/**
 * WSPRExciter - High-purity RF synthesis for WSPR-ease.
 * 
 * Optimized Architecture:
 * - Single 33-bit pipelined NCO (adds 2*M per 90MHz clock).
 * - Fast 4-bit mid-cycle overflow prediction.
 * - Walking ring advances by 0, 1, or 2 steps.
 */
module WSPRExciter (
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

  // --- Local Sync ---
  reg rst_l, txEn_l;
  always_ff @(posedge clk90) begin
    rst_l  <= reset;
    txEn_l <= txEnable;
  end

  // --- 1. Pipelined Tuning Word Delay matching ---
  wire [32:0] W = {tuningWord, 1'b0}; // 2*M
  
  reg [3:0] w_pipe[7:0][7:0];
  reg [7:0] w_bit32_pipe;

  always_ff @(posedge clk90) begin
    for (int s = 0; s < 8; s = s + 1) begin
      w_pipe[s][0] <= W[s*4 +: 4];
      for (int d = 1; d <= s; d = d + 1) begin
        w_pipe[s][d] <= w_pipe[s][d-1];
      end
    end
    w_bit32_pipe <= {w_bit32_pipe[6:0], W[32]};
  end

  // --- 2. Segmented 33-bit Accumulator ---
  reg [3:0] acc[7:0];
  reg       c[8:0];
  always_ff @(posedge clk90) begin
    if (rst_l) begin
      for (int s = 0; s < 8; s = s + 1) acc[s] <= 0;
      for (int s = 0; s < 9; s = s + 1) c[s]   <= 0;
    end else begin
      {c[0], acc[0]} <= acc[0] + w_pipe[0][0]; 
      for (int s = 1; s < 8; s = s + 1) begin
        {c[s], acc[s]} <= acc[s] + w_pipe[s][s] + c[s-1];
      end
      c[8] <= w_bit32_pipe[7] + c[7];
    end
  end

  // --- Predictive mid-cycle overflow ---
  reg comp_res;
  always_ff @(posedge clk90) begin
    comp_res <= (acc[7] >= {w_bit32_pipe[7], w_pipe[7][7][3:1]});
  end

  reg [1:0] ovf_full;
  always_ff @(posedge clk90) begin
    // Correct 2-bit addition of the MSB and the pipeline carry in the same cycle
    ovf_full <= w_bit32_pipe[7] + c[7];
  end

  reg [1:0] ovf_full_d1;
  reg ovf_mid;
  always_ff @(posedge clk90) begin
    ovf_full_d1 <= ovf_full;
    // Align mid-cycle prediction with the delayed full overflow
    ovf_mid <= (ovf_full == 2'd2) | ((ovf_full == 2'd1) & ~comp_res);
  end

  // --- 3. Walking Ring ---
  reg [5:0] ring = 6'b000001;
  function [5:0] advance1(input [5:0] r);
    advance1 = {r[4:0], r[5]};
  endfunction
  function [5:0] advance2(input [5:0] r);
    advance2 = {r[3:0], r[5:4]};
  endfunction

  always_ff @(posedge clk90) begin
    if (rst_l || !txEn_l) begin
      ring <= 6'b000001;
    end else begin
      case (ovf_full_d1)
        2'd0: ring <= ring;
        2'd1: ring <= advance1(ring);
        2'd2: ring <= advance2(ring);
        default: ring <= advance1(ring); // Safety fallback
      endcase
    end
  end

  // --- 4. Power Control ---
  reg [7:0] phaseEnd;
  always_ff @(posedge clk90) begin
    phaseEnd <= {acc[7], acc[6]};
  end
  
  reg en1, en2;
  reg [7:0] pwrThresh_l;
  always_ff @(posedge clk90) begin
    pwrThresh_l <= powerThreshold;
    en1 <= (phaseEnd < pwrThresh_l);
    en2 <= (phaseEnd < pwrThresh_l);
  end

  // --- 5. Output Mapping ---
  function [3:0] ringToGates(input [5:0] r, input en);
    logic [3:0] gates;
    begin
      gates[0] = r[0] | r[1] | r[2]; // Push Base
      gates[1] = r[1];               // Push Peak
      gates[2] = r[3] | r[4] | r[5]; // Pull Base
      gates[3] = r[4];               // Pull Peak
      ringToGates = gates & {4{en}};
    end
  endfunction

  reg [3:0] outR, outF;
  always_ff @(posedge clk90) begin
    // Rising Edge outputs the start of the 90MHz cycle
    outR <= ringToGates(ring, en2);
    // Falling Edge outputs the midpoint of the 90MHz cycle
    outF <= ringToGates(ovf_mid ? advance1(ring) : ring, en1);
  end

  reg [3:0] outR_reg, outF_reg;
  always_ff @(posedge clk90) begin
    outR_reg <= outR;
    outF_reg <= outF;
  end

  SB_IO #(.PIN_TYPE(6'b011000)) ioPB (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR_reg[0]), .D_OUT_1(outF_reg[0]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioPP (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR_reg[1]), .D_OUT_1(outF_reg[1]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLB (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR_reg[2]), .D_OUT_1(outF_reg[2]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLP (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR_reg[3]), .D_OUT_1(outF_reg[3]));

endmodule
