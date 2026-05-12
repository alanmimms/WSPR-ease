`timescale 1ns / 100ps
// Behavioral model for Lattice SB_IO
module SB_IO #(
    parameter [5:0] PIN_TYPE = 6'b000000,
    parameter [0:0] PULLUP = 1'b0,
    parameter [0:0] NEG_TRIGGER = 1'b0,
    parameter IO_STANDARD = "SB_LVCMOS"
) (
    inout  wire  PACKAGE_PIN,
    output logic PACKAGE_PIN_OUT,
    input  logic OUTPUT_CLK,
    input  logic CLOCK_ENABLE,
    input  logic OUTPUT_ENABLE,
    input  logic D_OUT_0,
    input  logic D_OUT_1,
    output logic D_IN_0,
    output logic D_IN_1,
    input  logic INPUT_CLK,
    input  logic LATCH_INPUT_VALUE
);

    logic dout_q_0 = 0;
    logic dout_q_1 = 0;
    
    // Sample the outputs on the clock edges
    always @(posedge OUTPUT_CLK) begin
        dout_q_0 <= D_OUT_0;
        dout_q_1 <= D_OUT_1; // Temporarily posedge
    end

    logic dout;
    always @(*) begin
        if (PIN_TYPE[3]) begin
            // 2'b10 = Combinatorial, 2'b11 = Inverted
            dout = PIN_TYPE[2] ? !dout_q_0 : D_OUT_0;
        end else begin
            if (PIN_TYPE[2]) begin
                // 2'b01 = Simple Registered (e.g., 6'b010101)
                dout = dout_q_0;
            end else begin
                // 2'b00 = DDR Registered (e.g., 6'b010001)
                dout = OUTPUT_CLK ? dout_q_0 : dout_q_1;
            end
        end
    end

    
    // For simulation, assume OUTPUT_ENABLE is 1 if not connected
    assign PACKAGE_PIN = dout;
    assign PACKAGE_PIN_OUT = PACKAGE_PIN;

endmodule
