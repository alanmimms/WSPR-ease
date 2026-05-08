`timescale 1ns / 100ps
`include "regs.sv"

module SPIRegisters (
    input  logic reset,

    // SPI Interface
    input  logic fpgaSCLK,
    input  logic fpgaMOSI,
    output logic fpgaMISO,
    input  logic fpgaNCS,

    // Destination Domain (90 MHz)
    input  logic clk_dest,
    output logic [47:0] tuningWord,
    output logic [7:0]  powerThresh,
    input  logic        pllLocked,
    output logic        txEnable,
    output logic        modeSquare,

    // Frequency counter values (from 90 MHz domain)
    input  logic [26:0] ppsCount,
    input  logic [4:0]  ppsGen
);

  // Synchronizers
  logic [1:0] syncPll;
  always_ff @(posedge fpgaSCLK) syncPll <= {syncPll[0], pllLocked};
  wire pll_spi = syncPll[1];

  logic [1:0] syncNCS;
  always_ff @(posedge clk_dest) syncNCS <= {syncNCS[0], fpgaNCS};
  wire ncs_dest = syncNCS[1];

  // Shadow registers
  logic [26:0] ppsCountShadow, ppsCountSpi;
  logic [4:0]  ppsGenShadow, ppsGenSpi;

  always_ff @(posedge clk_dest) begin
    if (ncs_dest) begin
      ppsCountShadow <= ppsCount;
      ppsGenShadow <= ppsGen;
    end
  end

  reg [5:0] bitCount = 0;
  always_ff @(posedge fpgaSCLK or posedge fpgaNCS) begin
    if (fpgaNCS) begin
      ppsCountSpi <= 0;
      ppsGenSpi <= 0;
    end else if (bitCount == 0) begin
      ppsCountSpi <= ppsCountShadow;
      ppsGenSpi <= ppsGenShadow;
    end
  end

  // SPI Protocol
  logic [31:0] twLowRaw = 0;
  logic [15:0] twHighRaw = 0;
  logic        isWrite = 0;
  logic [6:0]  selAddr = 0;
  logic [31:0] writeBuf = 0;
  logic [1:0]  ctrlSpi = 0;
  logic [31:0] readShift = 0;

  always_ff @(posedge fpgaSCLK or posedge fpgaNCS) begin
    if (fpgaNCS) begin
      bitCount <= 0;
      readShift <= 0;
      fpgaMISO <= 0;
    end else begin
      writeBuf <= {writeBuf[30:0], fpgaMOSI};
      
      if (bitCount == 0) begin
        isWrite <= fpgaMOSI;
      end else if (bitCount < 8) begin
        selAddr <= {selAddr[5:0], fpgaMOSI};
      end 
      
      if (bitCount == 8 && !isWrite) begin
        case (selAddr)
          aWSPRControl:    readShift <= {29'd0, pll_spi, ctrlSpi};
          aWSPRTuningLow:  readShift <= twLowRaw;
          aWSPRTuningHigh: readShift <= {16'd0, twHighRaw};
          aWSPRPPS:        readShift <= {ppsCountSpi, ppsGenSpi};
          aWSPRSig:        readShift <= eWSPRSigVal;
          default:         readShift <= 32'hDEADBEEF; 
        endcase
      end 
      
      if (bitCount >= 8 && !isWrite) begin
        fpgaMISO <= (bitCount == 8) ? readShift[31] : readShift[30];
        readShift <= {readShift[30:0], 1'b0};
      end
      
      if (bitCount == 39 && isWrite) begin
        if (selAddr == aWSPRControl)    ctrlSpi   <= {writeBuf[0], fpgaMOSI};
        if (selAddr == aWSPRTuningLow)  twLowRaw  <= {writeBuf[30:0], fpgaMOSI};
        if (selAddr == aWSPRTuningHigh) twHighRaw <= {writeBuf[14:0], fpgaMOSI};
      end
      
      bitCount <= bitCount + 1;
    end
  end

  // Dest domain sync
  logic ncsD1;
  logic [47:0] twMeta, twStable;
  logic [1:0]  ctrlMeta, ctrlStable;

  always_ff @(posedge clk_dest) begin
    ncsD1 <= ncs_dest;
    if (ncs_dest && !ncsD1) begin
      twMeta <= {twHighRaw, twLowRaw};
      ctrlMeta <= ctrlSpi;
    end
    twStable <= twMeta;
    ctrlStable <= ctrlMeta;

    if (reset) begin
      tuningWord <= 0;
      powerThresh <= 8'hFF;
      txEnable <= 0;
      modeSquare <= 0;
    end else begin
      tuningWord <= twStable;
      powerThresh <= 8'hFF;
      txEnable <= ctrlStable[0];
      modeSquare <= ctrlStable[1];
    end
  end

endmodule
