`timescale 1ns / 100ps
// Behavioral model for Lattice SB_MAC16
module SB_MAC16 (
    input  logic CLK, CE,
    input  logic [15:0] C, A, B, D,
    input  logic AHOLD, BHOLD, CHOLD, DHOLD,
    input  logic IRSTTOP, IRSTBOT,
    input  logic ORSTTOP, ORSTBOT,
    input  logic OLOADTOP, OLOADBOT,
    input  logic ADDSUBTOP, ADDSUBBOT,
    input  logic OHOLDTOP, OHOLDBOT,
    input  logic CI, ACCUMCI, SIGNEXTIN,
    output logic [31:0] O,
    output logic CO, ACCUMCO, SIGNEXTOUT
);
    parameter [0:0] NEG_TRIGGER = 0;
    parameter [0:0] C_REG = 0;
    parameter [0:0] A_REG = 0;
    parameter [0:0] B_REG = 0;
    parameter [0:0] D_REG = 0;
    parameter [0:0] TOP_8x8_MULT_REG = 0;
    parameter [0:0] BOT_8x8_MULT_REG = 0;
    parameter [0:0] PIPELINE_16x16_MULT_REG1 = 0;
    parameter [0:0] PIPELINE_16x16_MULT_REG2 = 0;
    parameter [1:0] TOPOUTPUT_SELECT = 0;
    parameter [1:0] TOPADDSUB_LOWERINPUT = 0;
    parameter [1:0] TOPADDSUB_UPPERINPUT = 0;
    parameter [1:0] TOPADDSUB_CARRYSELECT = 0;
    parameter [1:0] BOTOUTPUT_SELECT = 0;
    parameter [1:0] BOTADDSUB_LOWERINPUT = 0;
    parameter [1:0] BOTADDSUB_UPPERINPUT = 0;
    parameter [1:0] BOTADDSUB_CARRYSELECT = 0;
    parameter [0:0] MODE_8x8 = 0;
    parameter [0:0] A_SIGNED = 0;
    parameter [0:0] B_SIGNED = 0;

    // Internal Registers
    logic [15:0] r_top = 0;
    logic [15:0] r_bot = 0;
    
    // Multiplier
    wire [31:0] prod = A * B;
    
    // Adder Inputs
    wire [15:0] top_in_l = (TOPADDSUB_LOWERINPUT == 0) ? A : (TOPADDSUB_LOWERINPUT == 1) ? prod[15:0] : prod[31:16];
    wire [15:0] top_in_u = (TOPADDSUB_UPPERINPUT == 0) ? C : (TOPADDSUB_UPPERINPUT == 1) ? D : r_top;
    
    wire [15:0] bot_in_l = (BOTADDSUB_LOWERINPUT == 0) ? B : (BOTADDSUB_LOWERINPUT == 1) ? prod[15:0] : prod[31:16];
    wire [15:0] bot_in_u = (BOTADDSUB_UPPERINPUT == 0) ? C : (BOTADDSUB_UPPERINPUT == 1) ? D : r_bot;
    
    // Carries
    wire bot_ci = (BOTADDSUB_CARRYSELECT == 3) ? CI : (BOTADDSUB_CARRYSELECT == 2) ? ACCUMCI : 0;
    wire [16:0] bot_sum = bot_in_l + bot_in_u + bot_ci;
    
    wire top_ci = (TOPADDSUB_CARRYSELECT == 3) ? CI : (TOPADDSUB_CARRYSELECT == 2) ? bot_sum[16] : 0;
    wire [16:0] top_sum = top_in_l + top_in_u + top_ci;
    
    always @(posedge CLK) begin
        if (IRSTTOP || ORSTTOP) r_top <= 0;
        else if (CE && !OHOLDTOP) r_top <= top_sum[15:0];
        
        if (IRSTBOT || ORSTBOT) r_bot <= 0;
        else if (CE && !OHOLDBOT) r_bot <= bot_sum[15:0];
    end
    
    assign O[31:16] = (TOPOUTPUT_SELECT == 1) ? r_top : top_sum[15:0];
    assign O[15:0] = (BOTOUTPUT_SELECT == 1) ? r_bot : bot_sum[15:0];
    assign CO = top_sum[16];
    assign ACCUMCO = top_sum[16];
    assign SIGNEXTOUT = top_sum[15];

endmodule
