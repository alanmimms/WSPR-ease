# Project Context & Coding Guidelines

## Project Base Knowledge
Look in doc/OVERVIEW.md for overall info on the project and
doc/FPGA.md for details on what we're doing right now. 

## C & C++ Coding Standards

**General Style**
* **Indentation:** Use strictly **2-space** indentation.
* **Naming Convention:** Use **camelCase** for all variables,
  functions, and method names. Do NOT use `snake_case`.
    * *Example:* Use `nCall` instead of `n_call`.
* **Class Members:** Do NOT use the `m_` prefix for instance members.
* **Templates:** All template parameter names must be **ALL UPPERCASE**.

**Control Structures**
* **Spacing:** Insert exactly one space after C++ keywords (`if`,
  `for`, `switch`, `while`, etc.) before the opening parenthesis of
  the following expression.
    * *Correct:* `if (condition)`
    * *Incorrect:* `if(condition)`
* **Loops:** Declare loop variables locally within the `for` loop
  statement whenever possible.
    * *Example:* `for (int i = 0; ...)`
* **Indices:** Use `int` for generic index variables.

**Data Types**
* **Strings:** Use C-style string variables and arrays (`char[]`,
  `const char*`) instead of `std::string` or `std::string_view` to
  avoid verbosity and conversion overhead.

## Documentation

All documents we write will be in markdown format, with wrap at 80
columns applied to all paragraph text so it's readable in source form.
Each section or item will not be manually numbered by you to make the
document more easily maintained. Instead use markdown markup to
identify such entities so they are numbered by the markdown rendering
process used by the reader.

## Interaction Preferences
* **Tone:** Provide direct, technical responses. Do not include
  congratulations or attempts to validate intelligence.
* **Closing:** Do not end responses with prompts for further questions
  (e.g., "Let me know if..."). Simply complete the output and await
  the next command.

When this is all clear, wait for me to tell you next steps.
