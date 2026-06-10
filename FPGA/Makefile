# Makefile for WSPR-ease FPGA Simulation
# Integrates Verilator with ESP32 SW code

# Directories
FPGA_DIR := .
SW_DIR := ../sw
HAL_DIR := $(SW_DIR)/hal
SIM_DIR := sim
OBJDIR := obj_dir
GENHWDIR := gen-hw
GENSIMDIR := gen-sim

# Verilator configuration
VERILATOR := verilator
VERILATOR_FLAGS := -Wall -Wno-fatal --trace --cc --exe --build
VERILATOR_FLAGS += -Wno-PINMISSING -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
VERILATOR_FLAGS += -Wno-UNUSEDPARAM -Wno-UNDRIVEN -Wno-TIMESCALEMOD
VERILATOR_FLAGS += -Wno-GENUNNAMED -Wno-DECLFILENAME -Wno-PROCASSINIT -Wno-COMBDLY
VERILATOR_FLAGS += -I$(SIM_DIR)
VERILATOR_FLAGS += -I$(HAL_DIR)
VERILATOR_FLAGS += -sv
VERILATOR_FLAGS += -CFLAGS "-std=c++20 -I../../sw/src -I../../sw/hal -g"
VERILATOR_FLAGS += -LDFLAGS "-g -lm"

# Source files
RTL_SOURCES := $(GENHWDIR)/Top.v
RTL_SIM_SOURCES := $(GENSIMDIR)/Top.v

CPP_SOURCES := tbTop.cpp

# Ask Yosys where its data directory is located (works on Linux, Mac, Windows MSYS2)
YOSYS_DATDIR := $(shell yosys-config --datdir)

# Construct the path to the iCE40 simulation models
ICE40_SIM_MODELS := $(SIM_DIR)/cells_sim.v

AMARANTH_FILES := gateware.py build.py regs.py buildNumber.txt

PROJECT=wspr_ease
TOP=Top
PCF=pins.pcf
DEVICE=up5k
PACKAGE=sg48

# Output image and sim executable
FPGATARGET := build/fpga.img
SIMTARGET := obj_dir/VTop

.PHONY: all hw clean sim run fast superfast help test $(GENHWDIR) $(GENSIMDIR)

all: hw

# Build for FPGA deployment
$(RTL_SOURCES):	$(AMARANTH_FILES)
	@echo "--- Building FPGA SystemVerilog ---"
	SIMULATION=0 .venv/bin/python3 build.py
	sed -i '/\$$auto\$$verilog_backend/d' $(RTL_SOURCES)
	sed -i 's/\$$/_/g' $(RTL_SOURCES)

# Build for sim
$(RTL_SIM_SOURCES): $(AMARANTH_FILES)
	@echo "--- Building Simulation SystemVerilog ---"
	SIMULATION=1 .venv/bin/python3 build.py
	sed -i '/\$$auto\$$verilog_backend/d' $(RTL_SIM_SOURCES)
	sed -i 's/\$$/_/g' $(RTL_SIM_SOURCES)

# The FPGA image
hw $(FPGATARGET):	$(RTL_SOURCES) $(PCF) timing.py
	@echo "--- Building FPGA Bitstream ---"
	@mkdir -p $(GENHWDIR) build
	@echo "  YOSYS (Synthesis)..."
	yosys -q -p "read_verilog "$(RTL_SOURCES)"; synth_ice40 -dsp -top Top -json $(PROJECT).json"
	@echo "  NEXTPNR (Place & Route)..."
	nextpnr-ice40 --$(DEVICE) --package $(PACKAGE) --freq 90 --opt-timing --no-promote-globals \
		--pre-pack timing.py --placer heap --seed 42 \
		--json $(PROJECT).json --pcf $(PCF) \
		--log build/nextpnr.log -q \
		--asc $(PROJECT).asc
	@echo "  ICEPACK (Bitstream)..."
	icepack $(PROJECT).asc $(PROJECT).bin
	mv -f $(PROJECT).bin $(FPGATARGET)
	@echo "--- FPGA Build Complete ---"

# Build the Verilator simulation
sim $(SIMTARGET): $(RTL_SIM_SOURCES) $(CPP_SOURCES) $(HAL_DIR)/regs.hpp $(HAL_DIR)/buildNumber.hpp
	@touch $(CPP_SOURCES)
	@echo "Building Verilator simulation..."
	@mkdir -p $(GENSIMDIR) build
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-o VTop \
		--top-module Top \
		$(RTL_SIM_SOURCES) \
		$(ICE40_SIM_MODELS) \
		$(CPP_SOURCES)
	@echo "Build complete: $@"

# Run the simulation
run: sim $(SIMTARGET)
	@echo "Running simulation..."
	@./$(SIMTARGET)
	@echo "Simulation complete. Waveform saved to waveform.vcd"

test: fast

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
wave: waveform.vcd
	@if command -v gtkwave > /dev/null; then \
		gtkwave $< & \
	else \
		echo "gtkwave not found. Please install it to view waveforms."; \
	fi

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	@rm -rf build/
	@rm -rf $(OBJDIR)
	@rm -rf gen/*
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
