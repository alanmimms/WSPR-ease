#include "VTop.h"
#include "verilated.h"
#include "verilated_vcd_c.h"
#include <iostream>
#include <cstdint>
#include <memory>
#include <iomanip>
#include <algorithm>
#include <coroutine>
#include <exception>
#include <vector>

static const vluint64_t clockHz = 90ull * 1000ull * 1000ull;

static const char waveformFileName[] = "waveform.vcd";


constexpr uint64_t MEG(uint64_t m) { return m * 1000ull * 1000ull; }


struct SimTask {
  struct promise_type {
    SimTask get_return_object() { return {}; }
    std::suspend_never initial_suspend() { return {}; }
    std::suspend_never final_suspend() noexcept { return {}; }
    void return_void() {}
    void unhandled_exception() { std::terminate(); }
  };
};


class EventSource {
public:
  virtual ~EventSource() = default;

  // Returns the exact simulation time (in ps) of the next event.
  // Return UINT64_MAX if there are no pending events for this source.
  virtual uint64_t timeToNextEvent() const = 0;

  // Called by the main loop when the simulation time reaches this event's time.
  virtual void execute(uint64_t currentTime) = 0;
};

class ClockSource : public EventSource {
private:
  uint64_t halfPeriodPs;
  uint64_t nextEdgeTime;
  uint8_t* signal; // Pointer to the Verilator model's clock pin

public:
  ClockSource(uint64_t freqHz, uint8_t* sigPtr, uint64_t startTimePs = 0) 
    : signal(sigPtr), nextEdgeTime(startTimePs) {
        
    // Calculate the half-period in picoseconds
    uint64_t periodPs = 1000000000000ULL / freqHz;
    halfPeriodPs = periodPs / 2;
  }

  uint64_t timeToNextEvent() const override {
    return nextEdgeTime;
  }

  void execute(uint64_t currentTime) override {
    if (currentTime >= nextEdgeTime) {
      *signal = !(*signal); // Toggle the clock pin
      nextEdgeTime = currentTime + halfPeriodPs; // Schedule next edge
    }
  }
};

// A sticky note the Task Manager uses to remember why the coroutine went to sleep.
struct Waiter {
  std::coroutine_handle<> handle;
  uint8_t* signal;      // If nullptr, this is a purely time-based wait
  uint8_t targetValue;
  uint64_t wakeTimePs;  // The absolute simulation time to wake up
};


class TaskManager : public EventSource {
private:
  std::vector<Waiter> waitingTasks;

public:
  void addWaiter(std::coroutine_handle<> h, uint8_t* sig, uint8_t val) {
    waitingTasks.push_back({h, sig, val});
  }

  void addEdgeWaiter(std::coroutine_handle<> h, uint8_t* sig, uint8_t val) {
    waitingTasks.push_back({h, sig, val, UINT64_MAX});
  }

  void addTimeWaiter(std::coroutine_handle<> h, uint64_t wakeTime) {
    waitingTasks.push_back({h, nullptr, 0, wakeTime});
  }

  uint64_t timeToNextEvent() const override {
    return UINT64_MAX; 
  }

  // 2-Pass Execution Pattern
  void execute(uint64_t currentTime) override {
    std::vector<std::coroutine_handle<>> readyHandles;

    // Pass 1: Gather ready tasks and remove them from the waiting list
    auto it = waitingTasks.begin();
    while (it != waitingTasks.end()) {
      if (*(it->signal) == it->targetValue) {
	readyHandles.push_back(it->handle);
	it = waitingTasks.erase(it); // Returns new iterator
      } else {
	++it; // Manually increment if no erase
      }
    }

    // Pass 2: Resume the coroutines safely
    for (auto handle : readyHandles) {
      handle.resume();
    }
  }
};


struct WaitEdge {
  uint8_t* signal;
  uint8_t targetValue;
  TaskManager& tm;

  bool await_ready() const { return *signal == targetValue; }
    
  void await_suspend(std::coroutine_handle<> h) {
    tm.addEdgeWaiter(h, signal, targetValue);
  }
    
  void await_resume() const {}
};

struct WaitTime {
  uint64_t delayPs;
  uint64_t* currentTime;
  TaskManager& tm;

  bool await_ready() const { return delayPs == 0; }
    
  void await_suspend(std::coroutine_handle<> h) {
    tm.addTimeWaiter(h, *currentTime + delayPs);
  }
    
  void await_resume() const {}
};


// A high-level, linear SPI driver coroutine
// A true Async SPI Master Coroutine
SimTask spiWrite(VTop* top, TaskManager& tm, uint64_t* currentTime, uint8_t addr, uint32_t data) {
    uint64_t halfPeriodPs = 488281; // ~1.024 MHz SPI Clock
    
    // Drop Chip Select and ensure clock is low
    top->fpgaNCS = 0;
    top->fpgaSCLKpin = 0;
    
    // Wait a half-period before driving the first bit
    co_await WaitTime(halfPeriodPs, currentTime, tm);
    
    uint64_t payload = (static_cast<uint64_t>(addr) << 32) | data;

    for (int i = 39; i >= 0; i--) {
        // 1. Set MOSI data
        top->fpgaMOSI = (payload >> i) & 1;
        
        // 2. Wait for MOSI to settle (half period)
        co_await WaitTime(halfPeriodPs, currentTime, tm);
        
        // 3. Rising Edge (FPGA samples the data here)
        top->fpgaSCLKpin = 1;
        
        // 4. Wait half period
        co_await WaitTime(halfPeriodPs, currentTime, tm);
        
        // 5. Falling Edge
        top->fpgaSCLKpin = 0;
    }

    // Wait one final half-period before raising Chip Select
    co_await WaitTime(halfPeriodPs, currentTime, tm);
    top->fpgaNCS = 1;
}


static SimTask runTestSequence(VTop* top, TaskManager& tm, uint64_t* currentTime) {
  // 1. Assert Reset
  top->fpgaNRESET = 0;
    
  // 2. Wait 100ns (100,000 ps) for things to stabilize
  co_await WaitTime(100000, currentTime, tm);
    
  // 3. Clear Reset
  top->fpgaNRESET = 1;

  // 4. Wait for PLL Lock Delay (e.g., 10us / 10,000,000 ps)
  co_await WaitTime(10000000, currentTime, tm);

  // 5. Run SPI Transactions to configure the NCO
  // (In C++20, if we don't need to block the main sequence waiting for the SPI 
  // to finish, we can just call it and it will schedule itself into the TaskManager).
  spiWrite(top, tm, currentTime, 0x01, 0x0000FFFF); // Set NCO tuning word

  // 6. Wait for 10,000 NCO cycles (assuming 90MHz clock = ~11ns period)
  co_await WaitTime(10ull*1000ull * 11111ull, currentTime, tm);

  // 7. Assertions about the resulting state
#if 0
  if (top->rf_out == 0) {
    printf("ERROR: NCO did not output expected waveform!\n");
  } else {
    printf("SUCCESS: Sequence completed correctly.\n");
  }
#endif
}


/**
 * Calculates the NCO tuning word for a 48-bit accumulator.
 * Generalized for 32-bit architectures without 128-bit integer support.
 */
static uint64_t calculateNCOTuningWord(uint64_t freqHz, uint64_t ncoHz) {
  // Define constants for the 48-bit accumulator
  const uint64_t ncoShift = 48ULL;
  const uint64_t ncoScale = 1ULL << ncoShift;

  // Decompose ncoScale / ncoHz into quotient and remainder
  // ncoScale = (q * ncoHz) + r
  const uint64_t q = ncoScale / ncoHz;
  const uint64_t r = ncoScale % ncoHz;

  // result = (freqHz * q) + ((freqHz * r) / ncoHz)
  // Both intermediate products (freqHz * q) and (freqHz * r)
  // fit within 64 bits for standard HF frequencies.
  uint64_t term1 = freqHz * q;
  uint64_t term2 = (freqHz * r) / ncoHz;

  uint64_t tuningWord = term1 + term2;

  return tuningWord;
}


int main(int argc, char *argv[]) {
  VTop* top = new VTop();
  VerilatedVcdC* traceP = nullptr;
  
  Verilated::commandArgs(argc, argv);
  bool enableTrace = true;
  for (int i = 1; i < argc; i++) {
    if (std::string(argv[i]) == "--notrace") { enableTrace = false; }
  }

  if (enableTrace) {
    traceP = new VerilatedVcdC;
    Verilated::traceEverOn(true);
    top->trace(traceP, 99);
    traceP->open(waveformFileName);
  }

  TaskManager taskMgr;
    
  // FIX: Map strictly to the top-level pins Verilator actually exposes!
  ClockSource clk40(MEG(40), &top->clk40);
  ClockSource clk90(MEG(90), &top->clk90sim); // Pointing to the SIM pin, not internal PLL

  // Initialize all driving pins
  top->clk40 = 0;
  top->clk90sim = 0;
  top->fpgaNCS = 1;
  top->fpgaSCLKpin = 0;
  top->fpgaMOSI = 0;
  top->gnssPPS = 0;
  top->fpgaNRESET = 1;

  std::cout << "Starting simulation..." << std::endl;

  std::vector<EventSource*> sources = {&clk40, &clk90, &taskMgr};
  uint64_t curTime = 0;
  uint64_t MAX_SIM_TIME = 100000000ULL; // Ensure this is defined to prevent infinite loops

  // Set up coroutine that drives our test sequence.
  runTestSequence(top, taskMgr, &curTime);

  while (!Verilated::gotFinish() && curTime < MAX_SIM_TIME) {
    uint64_t nextTime = UINT64_MAX;
    for (auto* source : sources) {
      nextTime = std::min(nextTime, source->timeToNextEvent());
    }

    curTime = nextTime;

    // Fire hardware events
    for (auto* source : sources) {
      if (source->timeToNextEvent() <= curTime) {
        source->execute(curTime);
      }
    }

    top->eval();

    // Fire software/coroutine events
    taskMgr.execute(curTime);

    // Settle combinatorial logic resulting from coroutine outputs
    top->eval(); 

    if (traceP) traceP->dump(curTime);
  }

  if (traceP) {
    traceP->close();
    delete traceP;
  }

  delete top;
  std::cout << "Simulation finished. Waveform saved." << std::endl;
  return 0;
}
