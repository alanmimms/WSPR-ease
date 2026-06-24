#!/usr/bin/env python3
import sys
import os

class Field:

  def __init__(self, bits, default=0, doc="", signed=False):
    self.bits = bits
    self.default = default
    self.doc = doc
    self.signed = signed
    self.offset = 0
    self.name = ""

class UInt(Field):

  def __init__(self, default, bits, doc=""):
    super().__init__(bits, default, doc, signed=False)

class Int(Field):

  def __init__(self, default, bits, doc=""):
    super().__init__(bits, default, doc, signed=True)

class Bit(Field):

  def __init__(self, default, doc=""):
    super().__init__(1, default, doc, signed=False)

class Enum(Field):

  def __init__(self, default, bits, values, doc=""):
    super().__init__(bits, default, doc, signed=False)
    self.values = []
    lastVal = -1

    for v in values:

      if isinstance(v, str):
        name = v
        val = lastVal + 1
      elif isinstance(v, tuple):
        name, val = v
      else:
        raise ValueError(f"Invalid enum value format: {v}")

      self.values.append((name, val))
      lastVal = val

class Register:

  def __init__(self, name, addr, fieldsDict, doc=""):
    self.name = name
    self.addr = addr
    self.doc = doc
    self.fields = []
    self.fieldsByName = {}
    
    currentOffset = 0

    for fName, fObj in fieldsDict.items():
      fObj.name = fName
      fObj.offset = currentOffset
      self.fields.append(fObj)
      currentOffset += fObj.bits
    
    self.totalBits = currentOffset

  def __getitem__(self, key):

    if key not in self.fieldsByName:
      raise KeyError(f"Field '{key}' not found in register '{self.name}'")

    return self.fieldsByName[key]

  def __getattr__(self, name):
    """Allows dot-notation access to fields (e.g., my_reg.txEnable)"""
    if name in self.fieldsByName: return self.fieldsByName[name]
    raise AttributeError(f"'{type(self).__name__}' object has no field '{name}'")

class RegisterSet:

  def __init__(self, namespace):
    self.namespace = namespace
    self.registers = []
    self.regsByName = {}

  def register(self, addr, doc=""):

    def wrapper(cls):
      annotations = getattr(cls, "__annotations__", {})
      reg = Register(cls.__name__, addr, annotations, doc)
      self.registers.append(reg)
      self.regsByName[cls.__name__] = reg
      return cls

    return wrapper

  def __getitem__(self, key):

    if key not in self.regsByName:
      raise KeyError(f"Register '{key}' not found in RegisterSet '{self.namespace}'")

    return self.regsByName[key]

  def __getattr__(self, name):
    """Allows dot-notation access to registers (e.g., regs.Control)"""
    if name in self.regsByName: return self.regsByName[name]
    raise AttributeError(f"'{type(self).__name__}' object has no register '{name}'")

  def emitSv(self):
    lines = [f"// Generated Register Definitions for {self.namespace}", ""]
    
    # Collect all enums with their register context
    for reg in self.registers:

      for f in reg.fields:

        if isinstance(f, Enum):

          for vName, vVal in f.values:
            suffix = f"{vName[0].upper()}{vName[1:]}" if vName else ""
            paramName = f"e{self.namespace}{reg.name}{f.name[0].upper()}{f.name[1:]}{suffix}"
            lines.append(f"localparam logic [{f.bits-1}:0] {paramName} = {f.bits}'h{vVal:X};")

          lines.append("")

    for reg in self.registers:
      lines.append(f"typedef struct packed {{")

      for f in reversed(reg.fields):
        signedStr = "signed " if f.signed else ""
        bitsStr = f"[{f.bits-1}:0]" if f.bits > 1 else "     "
        
        if isinstance(f, Enum):
          typeStr = f"logic {bitsStr}"
        else:
          typeStr = f"logic {signedStr}{bitsStr}"
        
        docStr = f" // Bits {f.offset + f.bits - 1}:{f.offset}: {f.doc}" if f.doc else ""
        lines.append(f"  {typeStr} {f.name};{docStr}")

      lines.append(f"}} t{self.namespace}{reg.name};")
      
      initVals = [f"{f.bits}'h{f.default:X}" for f in reversed(reg.fields)]
      lines.append(f"localparam t{self.namespace}{reg.name} init{self.namespace}{reg.name} = {{{', '.join(initVals)}}};")
      lines.append("")
    
    for reg in self.registers:
      lines.append(f"localparam logic [6:0] a{self.namespace}{reg.name} = 7'h{reg.addr:02X};")

    return "\n".join(lines)

  def emitAmaranth(self):
    lines = [
      f"# Generated Amaranth Signatures for {self.namespace}",
      f"from amaranth import *",
      f"from amaranth.lib import enum, data",
      ""
    ]
    
    # Emit Enums
    for reg in self.registers:

      for f in reg.fields:

        if isinstance(f, Enum):
          enumName = f"{reg.name}{f.name[0].upper()}{f.name[1:]}"
          lines.append(f"class {enumName}(enum.Enum, shape={f.bits}):")

          for vName, vVal in f.values:
            suffix = f"{vName[0].upper()}{vName[1:]}" if vName else "Value"
            lines.append(f"    {suffix} = {vVal}")

          lines.append("")

    # Emit Structs using amaranth.lib.data.Struct
    for reg in self.registers:
      lines.append(f"class {reg.name}Struct(data.Struct):")

      for f in reg.fields:
        fName = f.name

        if fName == "reserved": fName = f"reserved{f.offset}"
        
        if isinstance(f, Enum):
          typeName = f"{reg.name}{f.name[0].upper()}{f.name[1:]}"
          lines.append(f"    {fName}: {typeName}")
        else:
          signedStr = ", signed=True" if f.signed else ""
          lines.append(f"    {fName}: unsigned({f.bits}){signedStr}")

      lines.append("")

    # Emit Address Map
    lines.append(f"class {self.namespace}Addr(enum.Enum, shape=7):")

    for reg in self.registers:
      lines.append(f"    {reg.name} = 0x{reg.addr:02X}")

    return "\n".join(lines)

  def emitCPP(self):
    lines = [f"// Generated Register Definitions for {self.namespace}", "#pragma once", "#include <cstdint>", ""]
    
    # Emit Enums
    for reg in self.registers:

      for f in reg.fields:

        if isinstance(f, Enum):
          typeName = f"{self.namespace}{reg.name}{f.name[0].upper()}{f.name[1:]}"
          lines.append(f"enum {typeName}: uint32_t {{")

          for vName, vVal in f.values:
            suffix = f"{vName[0].upper()}{vName[1:]}" if vName else ""
            lines.append(f"  e{typeName}{suffix} = {vVal},")

          lines.append(f"}};")
          lines.append("")

    # Emit Structs
    for reg in self.registers:
      if reg.doc: lines.append(f"// {reg.doc}")
      lines.append(f"struct __attribute__((packed, aligned(4))) {self.namespace}{reg.name} {{")
      lines.append("  union {")
      lines.append("    struct {")
      indent = "      "

      for f in reg.fields:
        typeStr = "int32_t" if f.signed else "uint32_t"

        if isinstance(f, Enum):
          typeStr = f"enum {self.namespace}{reg.name}{f.name[0].upper()}{f.name[1:]}"
        
        fName = f.name
        if fName == "reserved": fName = f"reserved{f.offset}"
        lines.append(f"{indent}{typeStr} {fName}: {f.bits};")
      
      lines.append("    };")

      if reg.totalBits <= 8:    uType = "uint8_t"
      elif reg.totalBits <= 16: uType = "uint16_t"
      elif reg.totalBits <= 32: uType = "uint32_t"
      else: uType = "uint64_t"

      lines.append(f"    {uType} u;")
      lines.append("  };")
      lines.append(f"}};")
      lines.append("")
    
    # Emit Address Enum
    lines.append(f"enum {self.namespace}Addr {{")

    for reg in self.registers:
      lines.append(f"  a{self.namespace}{reg.name} = 0x{reg.addr:02X},")

    lines.append(f"}};")
    return "\n".join(lines)

  def emitMD(self):
    lines = [f"# {self.namespace} Register Map", ""]

    for reg in self.registers:
      lines.append(f"## {reg.name} (Address: 0x{reg.addr:02X})")

      if reg.doc:
        lines.append(reg.doc)
        lines.append("")

      lines.append("### Bit Layout")
      lines.append("")
      header = "|"
      strip = "|"
      align = "|"

      for f in reversed(reg.fields):
        width = max(len(f.name), len(str(f.offset + f.bits - 1)) + 2)
        if f.name == "reserved":
          displayName = f"_reserved_[{f.bits}]"
        else:
          displayName = f"{f.name}[{f.bits}]"
        
        width = max(width, len(displayName))
        
        bitRange = f"{f.offset + f.bits - 1}"
        if f.bits > 1:
          bitRange += f"..{f.offset}"
        
        header += f" {bitRange:^{width}} |"
        strip  += f" {displayName:^{width}} |"
        align  += ":" + "-" * (width) + ":|"
      
      lines.append(header)
      lines.append(align)
      lines.append(strip)
      lines.append("")

      lines.append("| Bits | Field | Type | Default | Description |")
      lines.append("| :--- | :--- | :--- | :--- | :--- |")

      for f in reversed(reg.fields):
        bitRange = f"{f.offset + f.bits - 1}:{f.offset}" if f.bits > 1 else f"{f.offset}"
        fType = "UInt"

        if isinstance(f, Enum):
          fType = "Enum"
        elif isinstance(f, Bit):
          fType = "Bit"
        elif f.signed:
          fType = "Int"
        
        displayName = f.name
        if displayName == "reserved": displayName = f"_reserved_[{f.bits}]"
        lines.append(f"| {bitRange} | {displayName} | {fType} | {f.default} | {f.doc} |")

      lines.append("")
    return "\n".join(lines)

  def writeFiles(self, prefix):
    with open(f"{prefix}.hpp", "w") as f:
      f.write(self.emitCPP())
    with open(f"{prefix}.md", "w") as f:
      f.write(self.emitMD())
    with open(f"{prefix}_gen.py", "w") as f:
      f.write(self.emitAmaranth())

if __name__ == "__main__":
  print("Register Tool: Import this module in your definition script.")
