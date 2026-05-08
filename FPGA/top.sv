`timescale 1ns / 100ps

module Top (
	    input  logic clk40,
	    input logic gnssPPS,
	    input logic fpgaNRESET,
	    input  logic fpgaSCLK_pin,
	    input  logic fpgaMOSI,
	    output wire fpgaMISO,
	    input  logic fpgaNCS,
	    output logic rfPushBase,
	    output wire rfPushPeak,
	    output logic rfPullBase,
	    output wire rfPullPeak,
	    output logic driverNEN
	    );

  logic fpgaSCLK, pllLocked, pllLocked_gb;

  SB_GB sclkGbuf (.USER_SIGNAL_TO_GLOBAL_BUFFER(fpgaSCLK_pin), .GLOBAL_BUFFER_OUTPUT(fpgaSCLK));

  // Use a global buffer for the LOCK signal to move it away from the PLL tile and resolve placement issues.
  SB_GB pllLockGbuf (.USER_SIGNAL_TO_GLOBAL_BUFFER(pllLocked), .GLOBAL_BUFFER_OUTPUT(pllLocked_gb));

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


  logic rst90;
  Synchronizer #(1, 2) sync_rst90 (
    .clk(clk90),
    .dIn(!fpgaNRESET),
    .dOut(rst90)
  );

  // Sync pllLocked into clk90 domain
  logic pllLocked_clk90;
  Synchronizer #(1, 2) sync_pllLocked_clk90 (
    .clk(clk90),
    .dIn(pllLocked_gb),
    .dOut(pllLocked_clk90)
  );

  logic [47:0] tuningWord, tuningWord_d1;
  logic txEnable, txEnable_d1;
  logic modeSquare, modeSquare_d1;

  logic [26:0] ppsCount;
  logic [4:0]  ppsGen;

  FreqCounter freqCounterCore (
    .clk90(clk90),
    .reset(rst90),
    .fpgaNCS(fpgaNCS),
    .samplePPS(gnssPPS),
    .ppsCount(ppsCount),
    .ppsGen(ppsGen)
  );

  SPIRegisters spiCore (
			.reset(rst90),
			.fpgaSCLK(fpgaSCLK),
			.fpgaMOSI(fpgaMOSI),
			.fpgaMISO(fpgaMISO),
			.fpgaNCS(fpgaNCS),
			.clk_dest(clk90), 
			.tuningWord(tuningWord),
			.pllLocked(pllLocked_gb),
			.txEnable(txEnable),
			.modeSquare(modeSquare),
			.ppsCount(ppsCount),
			.ppsGen(ppsGen)
			);

  logic [47:0] twSPISync1;
  logic [47:0] twSPISync2;

  always_ff @(posedge clk90) begin
    // Register the SPI data twice into the 90MHz domain This allows
    // the tool to place these registers anywhere without impacting
    // the core NCO timing.
    twSPISync1 <= tuningWord;
    twSPISync2 <= twSPISync1;

    txEnable_d1 <= txEnable;
    modeSquare_d1 <= modeSquare;
  end

  Exciter exciterCore (
		       .reset(rst90),
		       .clk90(clk90), 
		       .tuningWord(twSPISync2),
		       .modeSquare(modeSquare_d1),
		       .txEnable(txEnable_d1 & pllLocked_clk90), 
		       .rfPushBase(rfPushBase),
		       .rfPushPeak(rfPushPeak),
		       .rfPullBase(rfPullBase),
		       .rfPullPeak(rfPullPeak)
		       );

  logic dEn;
  always_ff @(posedge clk90) dEn <= !(txEnable & pllLocked_clk90);
  assign driverNEN = dEn;

endmodule
