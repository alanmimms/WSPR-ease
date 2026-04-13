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
		     output logic [31:0] tuningWord = 0,
		     output logic [7:0]  powerThresh = 8'hFF,
		     input  logic        pllLocked,
		     output logic        txEnable,

		     // Frequency counter values (from 90 MHz domain)
		     input  logic [26:0] ppsCount,
		     input  logic [4:0]  ppsGen
		     );

  // =====================================================================
  // 1. Clock Domain Crossing (90 MHz -> SPI Domain)
  // =====================================================================
  logic pllLocked_spi;
  Synchronizer #(1, 2) sync_pllLocked (
    .clk(fpgaSCLK),
    .dIn(pllLocked),
    .dOut(pllLocked_spi)
  );

  // For multi-bit signals, we use a shadow register that freezes when SPI is active.
  // We use fpgaNCS (asynchronous to clk_dest) to freeze the shadow.
  // To avoid meta-stability on the freeze signal, we sync NCS into clk_dest.
  logic ncs_clk_dest;
  Synchronizer #(1, 2) sync_ncs_dest (
    .clk(clk_dest),
    .dIn(fpgaNCS),
    .dOut(ncs_clk_dest)
  );

  logic [26:0] ppsCount_shadow;
  logic [4:0]  ppsGen_shadow;

  always_ff @(posedge clk_dest) begin
    if (ncs_clk_dest) begin // SPI is idle
      ppsCount_shadow <= ppsCount;
      ppsGen_shadow   <= ppsGen;
    end
  end

  // =====================================================================
  // 2. SPI Domain Logic (Write & Readback)
  // =====================================================================
  logic [31:0] twRaw = 0;
  logic [5:0]  bitCount = 0;
  logic        isWrite = 0;
  logic [6:0]  selAddr = 0;
  logic [31:0] writeBuf = 0;
  
  tWSPRControl ctrlSPI = initWSPRControl;
  logic [31:0] readShift = 0;

  // Output on the negative edge to be stable for ESP32
  // We use the MSB of readShift.
  assign fpgaMISO = fpgaNCS ? 1'bZ : readShift[31];

  always_ff @(posedge fpgaSCLK or posedge fpgaNCS) begin
    if (fpgaNCS) begin
      bitCount <= '0;
      readShift <= '0;
    end else begin
      writeBuf <= {writeBuf[30:0], fpgaMOSI};
      
      if (bitCount == 0) begin
        isWrite <= fpgaMOSI;
      end else if (bitCount < 8) begin
        selAddr <= {selAddr[5:0], fpgaMOSI};
      end 
      
      // READBACK MUX
      // At bit 8, we have the address and know if it's a read.
      else if (bitCount == 8 && !isWrite) begin
        case (selAddr)
          aWSPRControl: readShift <= {ctrlSPI.powerThresh, 22'd0, pllLocked_spi, ctrlSPI.txEnable};
          aWSPRTuning:  readShift <= twRaw;
          aWSPRPPS:     readShift <= {ppsCount_shadow, ppsGen_shadow};
          aWSPRSig:     readShift <= eWSPRSigVal;
          default:      readShift <= 32'hDEADBEEF; 
        endcase
      end 
      
      // SHIFT OUT
      else if (bitCount >= 8 && !isWrite) begin
        readShift <= {readShift[30:0], 1'b0};
      end
      
      // WRITE COMMIT
      if (bitCount == 39 && isWrite) begin
        if (selAddr == aWSPRControl) ctrlSPI <= {writeBuf[30:0], fpgaMOSI};
        if (selAddr == aWSPRTuning)  twRaw   <= {writeBuf[30:0], fpgaMOSI};
      end
      
      bitCount <= bitCount + 1;
    end
  end

  // =====================================================================
  // 3. Destination Domain Sync (SPI -> 90 MHz)
  // =====================================================================
  logic ncs_sync_d1;
  logic [31:0] tw_sync;
  logic [7:0]  pwr_sync;
  logic        tx_sync;

  always_ff @(posedge clk_dest) begin
    ncs_sync_d1 <= ncs_clk_dest;

    if (reset) begin
      tw_sync     <= 0;
      pwr_sync    <= 8'hFF;
      tx_sync     <= 0;
      tuningWord  <= 0;
      powerThresh <= 8'hFF;
      txEnable    <= 0;
    end else begin
      // Transaction complete (NCS rising edge)
      if (ncs_clk_dest && !ncs_sync_d1) begin 
        tw_sync  <= twRaw;
        pwr_sync <= ctrlSPI.powerThresh;
        tx_sync  <= ctrlSPI.txEnable;
      end
      
      tuningWord  <= tw_sync;
      powerThresh <= pwr_sync;
      txEnable    <= tx_sync;
    end
  end

endmodule // SPIRegisters
