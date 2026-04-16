`timescale 1ns / 100ps
`default_nettype none

module ExciterFunctional #(
			  parameter bit SQUARE_WAVE = 0
			  ) (
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

  // --- 1. Full 32-bit Phase Accumulation ---
  reg [31:0] phase = 0;
  always_ff @(posedge clk90) begin
    if (reset) begin
      phase <= 0;
    end else begin
      phase <= phase + tuningWord;
    end
  end
  
  // --- 2. DDR Phase Calculation ---
  // Mid-cycle phase is current phase + M/2
  wire [31:0] phase_f = phase + {1'b0, tuningWord[31:1]};
  
  // --- 3. Duty Cycle Control ---
  wire en_r = txEnable && (phase[31:24] <= powerThreshold);
  wire en_f = txEnable && (phase_f[31:24] <= powerThreshold);
  
  // --- 4. Direct Phase-to-State Mapping ---
  // We use the exact same 14-bit truncation as the synthesizable model
  // N * 6 = (N * 4) + (N * 2)
  wire [16:0] sum_r = ({3'b0, phase[31:18]} << 2) + ({3'b0, phase[31:18]} << 1);
  wire [16:0] sum_f = ({3'b0, phase_f[31:18]} << 2) + ({3'b0, phase_f[31:18]} << 1);
  
  wire [2:0] state_r = sum_r[16:14];
  wire [2:0] state_f = sum_f[16:14];
  
  // --- 5. 1-2-1 Gate Mapping ---
  function [3:0] decode_wave(input [2:0] st, input en);
    logic [3:0] g;
    begin
      if (SQUARE_WAVE) begin
        // Simple square wave: 0-180 (states 0,1,2) -> Push Peak, 180-360 (3,4,5) -> Pull Peak
        if (st < 3) g = 4'b0010; // +2 (Push Peak)
        else        g = 4'b1000; // -2 (Pull Peak)
      end else begin
        // 1-2-1 modulation
        case (st)
          3'd0: g = 4'b0001; // +1
          3'd1: g = 4'b0010; // +2
          3'd2: g = 4'b0001; // +1
          3'd3: g = 4'b0100; // -1
          3'd4: g = 4'b1000; // -2
          3'd5: g = 4'b0100; // -1
          default: g = 4'b0000;
        endcase
      end
      decode_wave = g & {4{en}};
    end
  endfunction

  // --- 6. Output Registration ---
  reg [3:0] outR = 0, outF = 0;
  always_ff @(posedge clk90) begin
    outR <= decode_wave(state_r, en_r);
    outF <= decode_wave(state_f, en_f);
  end

  // --- 7. Standard Lattice I/O Primitives ---
  SB_IO #(.PIN_TYPE(6'b011000)) ioPB (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR[0]), .D_OUT_1(outF[0]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioPP (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR[1]), .D_OUT_1(outF[1]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLB (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(outR[2]), .D_OUT_1(outF[2]));
  SB_IO #(.PIN_TYPE(6'b011000)) ioLP (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(outR[3]), .D_OUT_1(outF[3]));

endmodule // ExciterFunctional
