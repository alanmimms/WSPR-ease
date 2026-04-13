`timescale 1ns / 100ps

module Top (
	    input  logic clk40,
	    input logic gnssPPS,
	    input logic fpgaNRESET,
	    input  logic fpgaSCLK_pin,
	    input  logic fpgaMOSI,
	    output logic fpgaMISO,
	    input  logic fpgaNCS,
	    output logic rfPushBase,
	    output wire rfPushPeak,
	    output logic rfPullBase,
	    output wire rfPullPeak,
	    output logic driverNEN
	    );

  logic fpgaSCLK, pllLocked;

  SB_GB sclkGbuf (.USER_SIGNAL_TO_GLOBAL_BUFFER(fpgaSCLK_pin), .GLOBAL_BUFFER_OUTPUT(fpgaSCLK));

// 90 MHz System Clock via Fabric Routing
  logic clk90_pre, clk90;
  
  SB_PLL40_PAD #(
    .FEEDBACK_PATH("SIMPLE"),
    .DIVR(4'b0000),         // DIVR = 0  (PFD = 40MHz)
    .DIVF(7'b0010001),      // DIVF = 17 (VCO = 720MHz)
    .DIVQ(3'b011),          // DIVQ = 3  (Output = 90MHz)
    .FILTER_RANGE(3'b010)   // CORRECT for 40MHz input
  ) sysPll (
    .PACKAGEPIN(clk40),     // Pin 35 is the dedicated PLL Pad
    .PLLOUTCORE(clk90_pre), // Output to fabric, NOT directly to global
    .LOCK(pllLocked),
    .RESETB(1'b1),
    .BYPASS(1'b0)
  );


  // Buffer the PLL output onto the global clock network exactly as your old version did
  SB_GB clk90Gbuf (
    .USER_SIGNAL_TO_GLOBAL_BUFFER(clk90_pre), 
    .GLOBAL_BUFFER_OUTPUT(clk90)
  );


  logic rst90_s1, rst90;
  always_ff @(posedge clk90) begin
    rst90_s1 <= !fpgaNRESET;
    rst90    <= rst90_s1;
  end

  // Sync pllLocked into clk90 domain
  logic pllLocked_s1, pllLocked_s2;
  always_ff @(posedge clk90) begin
    pllLocked_s1 <= pllLocked;
    pllLocked_s2 <= pllLocked_s1;
  end

  logic [7:0] powerThresh, powerThresh_d1;
  logic [31:0] tuningWord, tuningWord_d1;
  logic txEnable, txEnable_d1;

  SPIRegisters spiCore (
			.reset(rst90),
			.fpgaSCLK(fpgaSCLK),
			.fpgaMOSI(fpgaMOSI),
			.fpgaMISO(fpgaMISO),
			.fpgaNCS(fpgaNCS),
			.clk_dest(clk90), 
			.tuningWord(tuningWord),
			.powerThresh(powerThresh),
			.pllLocked(pllLocked),
			.txEnable(txEnable),
			.ppsCount(27'h0),
			.ppsGen(5'h0)
			);

  always_ff @(posedge clk90) begin
    tuningWord_d1 <= tuningWord;
    powerThresh_d1 <= powerThresh;
    txEnable_d1 <= txEnable;
  end

  WSPRExciter exciterCore (
			   .reset(rst90),
			   .clk90(clk90), 
			   .tuningWord(tuningWord_d1),
			   .powerThreshold(powerThresh_d1),
			   .txEnable(txEnable_d1 & pllLocked_s2), 
			   .rfPushBase(rfPushBase),
			   .rfPushPeak(rfPushPeak),
			   .rfPullBase(rfPullBase),
			   .rfPullPeak(rfPullPeak)
			   );

  logic dEn;
  always_ff @(posedge clk90) dEn <= !(txEnable & pllLocked_s2);
  SB_IO #(.PIN_TYPE(6'b010101)) ioD (.PACKAGE_PIN(driverNEN), .D_OUT_0(dEn));

endmodule
