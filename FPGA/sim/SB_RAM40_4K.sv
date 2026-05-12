`timescale 1ns / 1ps
// Behavioral model for Lattice SB_RAM40_4K (Dual-Port RAM)
module SB_RAM40_4K #(
    parameter [1:0] WRITE_MODE = 2'b00,
    parameter [1:0] READ_MODE = 2'b00
) (
    input  logic [10:0] WADDR,
    input  logic [15:0] WDATA,
    input  logic WE,
    input  logic WCLKE,
    input  logic WCLK,
    input  logic [10:0] RADDR,
    output logic [15:0] RDATA,
    input  logic RE,
    input  logic RCLKE,
    input  logic RCLK
);

    logic [15:0] mem [2047:0];
    
    always @(posedge WCLK) begin
        if (WCLKE && WE) begin
            mem[WADDR] <= WDATA;
        end
    end

    always @(posedge RCLK) begin
        if (RCLKE && RE) begin
            RDATA <= mem[RADDR];
        end
    end

endmodule
