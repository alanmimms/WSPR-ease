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
#include <variant>
#include <vector>

constexpr uint64_t MEG(uint64_t m) { return m * 1000ull * 1000ull; }
constexpr uint64_t SEC_TO_PS(uint64_t t) { return t * 1000ull * 1000ull * 1000ull * 1000ull; }

static const vluint64_t clockHz = MEG(90ull);
static const char waveformFileName[] = "waveform.vcd";

static uint64_t currentTime;	// Current sim time in ps

struct SimTask {

  struct promise_type {
    // Saves parent coroutine that called co_await on us
    std::coroutine_handle<> continuation = nullptr;

    SimTask get_return_object() {
      return SimTask{std::coroutine_handle<promise_type>::from_promise(*this)};
    }
        
    // Start running immediately upon creation until the first wait
    std::suspend_never initial_suspend() noexcept { return {}; }
        
    // The magic happens here: When this coroutine finishes, wake up the parent!
    auto final_suspend() noexcept {
      struct FinalAwaiter {
	bool await_ready() noexcept { return false; }
	std::coroutine_handle<> await_suspend(std::coroutine_handle<promise_type> h) noexcept {
	  // If a parent is waiting for us, return their handle to resume them instantly
	  if (h.promise().continuation) {
	    return h.promise().continuation;
	  }
	  // Otherwise, just yield back to the main simulation loop
	  return std::noop_coroutine();
	}
	void await_resume() noexcept {}
      };
      return FinalAwaiter{};
    }
        
    void return_void() {}
    void unhandled_exception() { std::terminate(); }
  };

  std::coroutine_handle<promise_type> handle;

  SimTask(std::coroutine_handle<promise_type> h) : handle(h) {}
    
  // --- Rule of 5: Safe C++ Memory Management to prevent memory leaks ---
  SimTask(const SimTask&) = delete;
  SimTask& operator=(const SimTask&) = delete;
  SimTask(SimTask&& other) noexcept : handle(other.handle) { other.handle = nullptr; }

  SimTask& operator=(SimTask&& other) noexcept {
    if (this != &other) {
      if (handle) handle.destroy();
      handle = other.handle;
      other.handle = nullptr;
    }
    return *this;
  }

  ~SimTask() { if (handle) handle.destroy(); }

  // Is the child already finished before we even tried to wait?
  bool await_ready() const noexcept { return !handle || handle.done(); }

  // Put the parent to sleep, and tell the child who the parent is.
  void await_suspend(std::coroutine_handle<> caller) noexcept {
    handle.promise().continuation = caller;
  }

  void await_resume() const noexcept {}
};


class EventSource {
public:
  virtual ~EventSource() = default;

  // Returns sim time (in ps) of the next event or UINT64_MAX if there
  // are no pending events for this source.
  virtual uint64_t timeToNextEvent() const = 0;

  // Called by the main loop when sim time reaches this event's time.
  virtual void execute() = 0;
};

class ClockSource : public EventSource {
private:
  uint64_t halfPeriodPS;
  uint64_t nextEdgeTimePS;
  CData* clkPin;

public:
  ClockSource(uint64_t freqHz, CData* pin) 
    : clkPin(pin), nextEdgeTimePS(0) 
  {
    uint64_t periodPS = SEC_TO_PS(1) / freqHz;
    halfPeriodPS = periodPS / 2;
  }

  uint64_t timeToNextEvent() const override { return nextEdgeTimePS; }

  void execute() override {

    if (currentTime >= nextEdgeTimePS) {
      *clkPin = !*clkPin;
      nextEdgeTimePS += halfPeriodPS;
    }
  }
};

struct TimeCondition {
  uint64_t wakeTime;		// Absolute time to next awaken
  bool isReady() const { return currentTime >= wakeTime; }
};


struct SignalCondition {
  CData *signalP;
  CData expectedValue;
  bool isReady() const { return *signalP == expectedValue; }
};


class TaskManager : public EventSource {
public:

  struct Waiter {
    std::coroutine_handle<> handle;
    std::variant<TimeCondition, SignalCondition> condition;
  };

  std::vector<Waiter> waitingTasks;

  template <typename ConditionType>
  void waitFor(std::coroutine_handle<> h, ConditionType cond) {
    waitingTasks.push_back({h, cond});
  }

  // --- REGISTRATION API ---
  // These are called by the Awaitables to push tasks into the list
  void waitForTime(std::coroutine_handle<> h, uint64_t wakeTime) {
    waitingTasks.push_back({h, TimeCondition{wakeTime}});
  }

  void waitForSignal(std::coroutine_handle<> h, CData* signal, CData value) {
    waitingTasks.push_back({h, SignalCondition{signal, value}});
  }

  uint64_t timeToNextEvent() const override {
    return UINT64_MAX; 
  }

  // 2-Pass Execution Pattern - called once to do sim loop.
  void execute() override {
    std::vector<std::coroutine_handle<>> readyTasks;

    // Pass 1: Gather ready tasks and remove them from the waiting list
    for (auto it = waitingTasks.begin(); it != waitingTasks.end(); ) {
      bool ready = std::visit([](auto &&cond) { return cond.isReady(); }, it->condition);

      if (ready) {
	readyTasks.push_back(it->handle);
	it = waitingTasks.erase(it); // Returns new iterator
      } else {
	++it; // Manually increment if no erase
      }
    }

    // Pass 2: Resume the ready tasks
    for (auto h: readyTasks) {
      if (h && !h.done()) h.resume();
    }
  }
};

static TaskManager theTM;


struct WaitEdge {
  CData* signal;
  CData targetValue;

  bool await_ready() const { return *signal == targetValue; }
  void await_suspend(std::coroutine_handle<> h) { theTM.waitForSignal(h, signal, targetValue); }
  void await_resume() const {}
};

struct WaitTime {
  uint64_t delayPS;

  bool await_ready() const { return delayPS == 0; }
  void await_suspend(std::coroutine_handle<> h) { theTM.waitForTime(h, currentTime + delayPS); }
  void await_resume() const {}
};


// A high-level, linear SPI driver coroutine
// A true Async SPI Master Coroutine
SimTask spiWrite(VTop* top, uint8_t addr, uint32_t data) {
  const uint64_t halfPeriodPS = 488281; // ~1.024 MHz SPI Clock
    
  top->fpgaNCS = 0;
  top->fpgaSCLKpin = 0;
  co_await WaitTime{halfPeriodPS};
    
  addr |= 0x80;		 // It's a write.
  uint64_t payload = (static_cast<uint64_t>(addr) << 32) | data;

  for (int i = 39; i >= 0; i--) {
    top->fpgaMOSI = (payload >> i) & 1;
    co_await WaitTime{halfPeriodPS};
    top->fpgaSCLKpin = 1;
    co_await WaitTime{halfPeriodPS};
    top->fpgaSCLKpin = 0;
  }

  co_await WaitTime{halfPeriodPS};
  top->fpgaNCS = 1;
  co_await WaitTime{halfPeriodPS};
}

// A true Async SPI Read Coroutine
SimTask spiRead(VTop* top, uint8_t addr, uint32_t &dataOut) {
  const uint64_t halfPeriodPS = 488281; // ~1.024 MHz SPI Clock
    
  top->fpgaNCS = 0;
  top->fpgaSCLKpin = 0;
  co_await WaitTime{halfPeriodPS};
    
  addr &= 0x7F;		 // It's a read (MSB is 0).
  uint64_t payload = static_cast<uint64_t>(addr) << 32;

  uint32_t readVal = 0;

  for (int i = 39; i >= 0; i--) {
    top->fpgaMOSI = (payload >> i) & 1;
    co_await WaitTime{halfPeriodPS};
    top->fpgaSCLKpin = 1;
    
    // Sample MISO while SCLK is high
    if (i < 32) {
      readVal = (readVal << 1) | (top->fpgaMISO & 1);
    }
    
    co_await WaitTime{halfPeriodPS};
    top->fpgaSCLKpin = 0;
  }

  dataOut = readVal;

  co_await WaitTime{halfPeriodPS};
  top->fpgaNCS = 1;
  co_await WaitTime{halfPeriodPS};
}


static SimTask runTestSequence(VTop* top) {
  // Hold reset for 100ns (100,000 ps) for things to stabilize
  top->fpgaNRESET = 0;
  co_await WaitTime{100000};
  top->fpgaNRESET = 1;

  // Wait 10us for PLL Lock
  co_await WaitTime{MEG(10)};

  // Read Hardware Signature register to verify SPI read
  uint32_t sig = 0;
  co_await spiRead(top, 0x0F, sig);
  std::cout << "SPI: Read Signature register (0x0F): 0x" 
            << std::hex << std::setw(8) << std::setfill('0') << sig << std::dec << std::endl;
  if (sig == 0x52505357) {
    std::cout << "SPI: Signature matches 'WSPR' (0x52505357) - Success!" << std::endl;
  } else {
    std::cout << "SPI ERROR: Signature mismatch! Expected 0x52505357, got 0x" 
              << std::hex << sig << std::dec << std::endl;
  }

  // Configure the NCO
  uint64_t tw = 17375000000000ull;
  co_await spiWrite(top, 0x01, (uint32_t) tw);
  co_await spiWrite(top, 0x02, (uint32_t) (tw >> 32));

  // Enable transmitter in CONTROL register (set txEnable = 1)
  co_await spiWrite(top, 0x00, 1);
  std::cout << "SPI: Wrote 0x00000001 to CONTROL register (0x00) - TX Enabled." << std::endl;

  // Wait 10,000 NCO cycles (assuming 90MHz clock = ~11ns period)
  co_await WaitTime{10000 * 11111};
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

  ClockSource clk40(MEG(40), &top->clk40);
  ClockSource clk90(MEG(90), &top->clk90sim); // Pointing to the SIM pin, not internal PLL

  // Initialize all driving pins
  top->clk40 = 0;
  top->clk90sim = 0;
  top->fpgaNCS = 1;
  top->fpgaSCLKpin = 0;
  top->fpgaMOSI = 0;
  top->gnssPPS = 0;
  top->fpgaNRESET = 0;

  std::cout << "Starting simulation..." << std::endl;

  std::vector<EventSource*> sources = {&clk40, &clk90, &theTM};
  uint64_t MAX_SIM_TIME = MEG(1000); // 1ms to prevent infinite loops

  // Set up coroutine that drives our test sequence.
  auto testSeqTask = runTestSequence(top);

  uint64_t rfTransitions = 0;
  uint8_t lastRF = 0;

  while (!Verilated::gotFinish() && currentTime < MAX_SIM_TIME) {
    uint64_t nextTime = UINT64_MAX;

    for (auto* source : sources) {
      nextTime = std::min(nextTime, source->timeToNextEvent());
    }

    currentTime = nextTime;

    // Fire events
    for (auto* source : sources) {
      if (source->timeToNextEvent() <= currentTime) source->execute();
    }

    top->eval();
    theTM.execute();
    top->eval(); 

    uint8_t currentRF = (top->rfPushBase << 3) | (top->rfPushPeak << 2) | (top->rfPullBase << 1) | top->rfPullPeak;
    if (currentRF != lastRF) {
      rfTransitions++;
      lastRF = currentRF;
    }

    if (traceP) traceP->dump(currentTime);
  }

  if (traceP) {
    traceP->close();
    delete traceP;
  }

  std::cout << "=== Simulation Summary ===" << std::endl;
  std::cout << "Simulation time: " << (currentTime / 1000000ull) << " us" << std::endl;
  std::cout << "RF pin transitions: " << rfTransitions << std::endl;
  if (rfTransitions > 0) {
    std::cout << "SUCCESS: RF outputs toggled successfully!" << std::endl;
  } else {
    std::cout << "FAILURE: No RF output activity detected." << std::endl;
  }

  delete top;
  std::cout << "Simulation finished. Waveform saved." << std::endl;
  return 0;
}
