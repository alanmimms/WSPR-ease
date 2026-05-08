`timescale 1ns / 100ps
`default_nettype none

module Exciter (
		input  wire        clk90,
		input  wire        reset,
		input  wire [47:0] tuningWord,
		input  wire        modeSquare,
		input  wire        txEnable,

		output wire        rfPushBase,
		output wire        rfPushPeak,
		output wire        rfPullBase,
		output wire        rfPullPeak
		);

  // DDR signal component naming convention, trailing
  // R==>rising edge signal
  // F==>falling edge signal

  // --- STAGE 1: Input Registration & LFSR Dither (T=1) ---
  logic [47:0] tw1;
  logic mode1, tx1, rst1;
  logic [15:0] lfsr;

  always_ff @(posedge clk90) begin
    tw1 <= tuningWord;
    mode1 <= modeSquare;
    tx1 <= txEnable;
    rst1 <= reset;
    lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
  end

  // Next stage(es) are a pipelined NCO via Amaranth.
  wire [47:0] ncoPhase;

  PipelinedNCO auto_nco (
      .clk(clk90),                // Amaranth auto-generates clock/reset ports
      .rst(rst1),
      .tw(tw1),
      .phase(ncoPhase)
  );

  // --- STAGE 2-4: Skewed Pipeline NCO (T=2,3,4) ---
  // We break the 48-bit addition into three 16-bit cycles.
  // This removes the 48-bit carry-chain bottleneck.
  
  logic [15:0] accL, accM, accH;
  logic carryL, carryM;
  logic [31:16] twMpipe;
  logic [47:32] twHpipe1, twHpipe2;

  always_ff @(posedge clk90) begin
    if (rst1) begin
      {accL, accM, accH} <= 48'h0;
      {carryL, carryM}   <= 2'b0;
    end else begin
      // Cycle 1: Low 16 bits
      {carryL, accL} <= accL + tw1[15:0];
      twMpipe <= tw1[31:16];
      twHpipe1 <= tw1[47:32];

      // Cycle 2: Mid 16 bits (receives carry from cycle 1)
      {carryM, accM} <= accM + twMpipe + carryL;
      twHpipe2 <= twHpipe1;

      // Cycle 3: High 16 bits (receives carry from cycle 2)
      accH <= accH + twHpipe2 + carryM;
    end
  end

  // --- STAGE 5: Phase Capture & DDR Offset (T=5) ---
  logic [15:0] phR, phF;
  logic mode5, tx5, sq5;
  logic [7:0] dither5;

  always_ff @(posedge clk90) begin
    phR <= accH;
    phF <= accH + (twHpipe2 >> 1); // 180-degree offset
    sq5 <= accH[15]; 
    dither5 <= lfsr[7:0]; 
    mode5 <= mode1; // Note: may need to delay mode/tx to match NCO pipe
    tx5 <= tx1;
  end

  // --- STAGE 6: Fabric Multiplier by 6 (T=6) ---
  // Logic: (Phase * 4) + (Phase * 2) + Dither
  logic [18:0] mulR, mulF;
  logic mode6, tx6, sq6;

  // Force MAC pattern.
  always_ff @(posedge clk90) begin
    mulR <= (phR * 19'd6) + dither5;
    mulF <= (phF * 19'd6) + dither5;
    sq6 <= sq5;
    mode6 <= mode5;
    tx6 <= tx5;
  end

  // --- STAGE 7: Decoder & Output Selection (T=7) ---
  logic pushBaseR, pushPeakR, pushBaseF, pushPeakF;
  logic pullBaseR, pullPeakR, pullBaseF, pullPeakF;
  logic pushBaseRpre, pushPeakRpre, pushBaseFpre, pushPeakFpre;
  logic pullBaseRpre, pullPeakRpre, pullBaseFpre, pullPeakFpre;
  logic txFinal;

  // Bits [18:16] of the multiplication are the 0-5 state index
  wire [2:0] stXR = mulR[18:16];
  wire [2:0] stXF = mulF[18:16];

  always_ff @(posedge clk90) begin
    txFinal <= tx6;

    if (mode6) begin
      // Mode 1: Square Wave
      pushBaseR <= sq6;
      pushPeakR <= sq6;
      pushBaseF <= sq6;
      pushPeakF <= sq6;

      pullBaseR <= !sq6;
      pullPeakR <= !sq6;
      pullBaseF <= !sq6;
      pullPeakF <= !sq6;
    end else begin
      // Mode 0: 1-2-1 Decoding pipelined
      pushBaseR <= pushBaseRpre;
      pushPeakR <= pushPeakRpre;
      pullBaseR <= pullBaseRpre;
      pullPeakR <= pullPeakRpre;

      pushBaseRpre <= (stXR == 3'd0 || stXR == 3'd2) && txFinal;
      pushPeakRpre <= (stXR == 3'd1) && txFinal;
      pullBaseRpre <= (stXR == 3'd3 || stXR == 3'd5) && txFinal;
      pullPeakRpre <= (stXR == 3'd4) && txFinal;
      
      pushBaseF <= pushBaseFpre;
      pushPeakF <= pushPeakFpre;
      pullBaseF <= pullBaseFpre;
      pullPeakF <= pullPeakFpre;

      pushBaseFpre <= txFinal && (stXF == 3'd0 || stXF == 3'd2);
      pushPeakFpre <= txFinal && (stXF == 3'd1);
      pullBaseFpre <= txFinal && (stXF == 3'd3 || stXF == 3'd5);
      pullPeakFpre <= txFinal && (stXF == 3'd4);
    end
  end

  // --- STAGE 8: Physical DDR Pin Drive ---
  SB_IO #(.PIN_TYPE(6'b010001)) io_pb (.PACKAGE_PIN(rfPushBase), .OUTPUT_CLK(clk90), .D_OUT_0(pushBaseR), .D_OUT_1(pushBaseF));
  SB_IO #(.PIN_TYPE(6'b010001)) io_pp (.PACKAGE_PIN(rfPushPeak), .OUTPUT_CLK(clk90), .D_OUT_0(pushPeakR), .D_OUT_1(pushPeakF));
  SB_IO #(.PIN_TYPE(6'b010001)) io_lb (.PACKAGE_PIN(rfPullBase), .OUTPUT_CLK(clk90), .D_OUT_0(pullBaseR), .D_OUT_1(pullBaseF));
  SB_IO #(.PIN_TYPE(6'b010001)) io_lp (.PACKAGE_PIN(rfPullPeak), .OUTPUT_CLK(clk90), .D_OUT_0(pullPeakR), .D_OUT_1(pullPeakF));

endmodule // Exciter
