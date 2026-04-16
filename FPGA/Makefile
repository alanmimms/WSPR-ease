# Makefile for WSPR-ease FPGA Simulation
# Integrates Verilator with ESP32 SW code

# Directories
FPGA_DIR := .
SW_DIR := ../sw
SIM_DIR := sim
OBJDIR := build

# Verilator configuration
VERILATOR := verilator
VERILATOR_FLAGS := -Wall -Wno-fatal --trace --cc --exe --build
VERILATOR_FLAGS += -I$(SIM_DIR)
VERILATOR_FLAGS += -CFLAGS "-std=c++17 -I$(SW_DIR)"
VERILATOR_FLAGS += -LDFLAGS "-lm"

# Source files
RTL_SOURCES := top.sv exciter.sv SPIRegisters.sv freqCounter.sv syncronizer.sv edgeDetector.sv
RTL_SIM_SOURCES += $(SIM_DIR)/sbIO.sv $(SIM_DIR)/sbPLL40Core.sv $(SIM_DIR)/sbPLL40Pad.sv $(SIM_DIR)/sbRAM404K.sv $(SIM_DIR)/sbGB.sv
GENERATED_SOURCES := regs.sv regs.hpp regs.md

CPP_SOURCES := tbTop.cpp

PROJECT=wspr_ease
TOP=Top
PCF=pins.pcf
DEVICE=up5k
PACKAGE=sg48

# Output image and sim executable
TARGET := build/fpga.img
SIMTARGET := obj_dir/VTop

.PHONY: all clean run sim fast superfast help

all: $(TARGET)

# The FPGA image
$(TARGET):	$(RTL_SOURCES) $(GENERATED_SOURCES)
	@echo "--- Building FPGA Bitstream ---"
	@mkdir -p build
	@echo "  YOSYS (Synthesis)..."
	yosys -q -p "read_verilog -sv $(RTL_SOURCES); synth_ice40 -dsp -top $(TOP) -json $(PROJECT).json"
	@echo "  NEXTPNR (Place & Route)..."
	nextpnr-ice40 --$(DEVICE) --package $(PACKAGE) --freq 90 --opt-timing --no-promote-globals \
		--pre-pack timing.py --placer heap --seed 1 \
		--json $(PROJECT).json --pcf $(PCF) \
		--log build/nextpnr.log -q \
		--asc $(PROJECT).asc
	@echo "  ICEPACK (Bitstream)..."
	icepack $(PROJECT).asc $(PROJECT).bin
	mv -f $(PROJECT).bin $(TARGET)
	@echo "--- FPGA Build Complete ---"

$(GENERATED_SOURCES):	regs.py ../tools/regTool.py
	export PYTHONPATH=$PYTHONPATH:..:. && python3 regs.py

# Build the Verilator simulation
$(SIMTARGET): $(RTL_SOURCES) $(RTL_SIM_SOURCES) $(CPP_SOURCES) simHAL.hpp
	@echo "Building Verilator simulation..."
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-o VTop \
		--top-module Top \
		$(RTL_SOURCES) \
		$(RTL_SIM_SOURCES) \
		$(CPP_SOURCES)
	@echo "Build complete: $@"

# Run the simulation
run: $(SIMTARGET)
	@echo "Running simulation..."
	@./$(SIMTARGET)
	@echo "Simulation complete. Waveform saved to waveform.vcd"

# Alias for run
sim: run

# Run fast simulation (no waveforms)
fast: $(SIMTARGET)
	@echo "Running fast simulation (no waveform trace)..."
	@./$(SIMTARGET) --notrace
	@echo "Fast simulation complete."

# Run superfast simulation (event-driven, no waveforms)
superfast: $(SIMTARGET)
	@echo "Running superfast simulation (event-driven fast-forward)..."
	@./$(SIMTARGET) --notrace --fastforward
	@echo "Superfast simulation complete."

# View waveform with gtkwave (if available)
wave: $(OBJDIR)/waveform.vcd
	@if command -v gtkwave > /dev/null; then \
		gtkwave $< & \
	else \
		echo "gtkwave not found. Please install it to view waveforms."; \
	fi

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	@rm -rf $(OBJDIR)
	@rm -f waveform.vcd
	@echo "Clean complete."

# Help target
help:
	@echo "WSPR-ease FPGA Simulation Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  all       - Build the simulation (default)"
	@echo "  run       - Build and run simulation with waveform tracing (~50s)"
	@echo "  fast      - Build and run fast simulation without waveforms (~7.6s)"
	@echo "  superfast - Build and run with event-driven fast-forward (~0.1s)"
	@echo "  sim       - Alias for 'run'"
	@echo "  wave      - Open waveform viewer (gtkwave)"
	@echo "  clean     - Remove all build artifacts"
	@echo "  help      - Show this help message"
	@echo ""
	@echo "The simulation integrates:"
	@echo "  - FPGA RTL (top.sv, sequencer.sv)"
	@echo "  - ESP32 SW code (transmitter.cpp, wspr-encoder.cpp)"
	@echo "  - Simulation HAL (simHAL.hpp)"
