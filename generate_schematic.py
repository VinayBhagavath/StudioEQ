#!/usr/bin/env python3
"""Generate spectrum_analyzer.kicad_sch — 2x3 LED matrix music visualizer.

Matches the built hardware (see README.md / music_visualizer.ino):
  - Arduino Uno R3 -> 74HC595 shift register drives 3 columns (Q1/Q2/Q3)
  - 2x NPN transistor (2N2222) sink the 2 rows
  - 6x LED + 220 ohm column resistors, 2x 1k transistor base resistors
Pinout: D10=SER/DATA, D11=RCLK/LATCH, D12=SRCLK/CLK, D2/D3=row bases.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
PROJECT = "spectrum_analyzer"
SYMS_DIR = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")
OUT = Path(__file__).parent / "spectrum_analyzer.kicad_sch"
ERC_RPT = Path(__file__).parent / "ERC.rpt"
GRID = 1.27
KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")

LIB_MAP = {
    "Device:R": ("Device.kicad_sym", "R"),
    "Device:LED": ("Device.kicad_sym", "LED"),
    "Transistor_BJT:Q_NPN_EBC": ("Transistor_BJT.kicad_sym", "Q_NPN_EBC"),
    "74xx:74HC595": ("74xx.kicad_sym", "74HC595"),
    "Connector_Generic:Conn_01x07": ("Connector_Generic.kicad_sym", "Conn_01x07"),
    "power:+5V": ("power.kicad_sym", "+5V"),
    "power:GND": ("power.kicad_sym", "GND"),
    "power:PWR_FLAG": ("power.kicad_sym", "PWR_FLAG"),
}

# Matrix size (as actually built)
NUM_ROWS = 2
NUM_COLS = 3

# Arduino Uno pins broken out on the 7-pin header J1 (Connector_Generic Conn_01x07)
AR_PINS = {
    "D2": "1",   # Row 1 transistor base (via 1k)
    "D3": "2",   # Row 2 transistor base (via 1k)
    "D10": "3",  # 74HC595 SER  (data)
    "D11": "4",  # 74HC595 RCLK (latch)
    "D12": "5",  # 74HC595 SRCLK (clock)
    "+5V": "6",
    "GND": "7",
}

ROW_SIGS = ["D2", "D3"]
# (Arduino signal, 74HC595 chip pin, net label, vertical bus x)
#   D10 -> SER  (pin 14, data),  D11 -> RCLK (pin 12, latch),  D12 -> SRCLK (pin 11, clock)
SHIFT_SIGS = [("D10", "14", "DATA", 95.0), ("D11", "12", "LATCH", 110.0), ("D12", "11", "CLK", 125.0)]
# Columns 1,2,3 driven by Q1,Q2,Q3 (74HC595 QB/QC/QD = chip pins 1/2/3); Q0 (QA) unused.
COL_Q_PINS = ["1", "2", "3"]
# Unused shift-register outputs: QA(15), QE(4), QF(5), QG(6), QH(7), QH'(9)
UNUSED_Q_PINS = ["15", "4", "5", "6", "7", "9"]


def uid() -> str:
    return str(uuid.uuid4())


def snap(v: float) -> float:
    return round(v / GRID) * GRID


def sym_pos(x: float, y: float) -> tuple[float, float]:
    return snap(x), snap(y)


def extract_symbol(lib_file: Path, sym_name: str) -> str:
    text = lib_file.read_text()
    needle = f'(symbol "{sym_name}"'
    start = text.find(needle)
    if start < 0:
        raise ValueError(f"Symbol {sym_name!r} not found in {lib_file}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError(f"Unclosed symbol {sym_name}")


def lib_symbol_block(lib_id: str) -> str:
    lib_file, sym_name = LIB_MAP[lib_id]
    body = extract_symbol(SYMS_DIR / lib_file, sym_name)
    return body.replace(f'(symbol "{sym_name}"', f'(symbol "{lib_id}"', 1)


def iter_pin_blocks(body: str):
    i = 0
    while True:
        start = body.find("(pin ", i)
        if start < 0:
            break
        depth = 0
        for j in range(start, len(body)):
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
                if depth == 0:
                    yield body[start : j + 1]
                    i = j + 1
                    break


def pin_def(lib_id: str, pin_num: str) -> tuple[float, float, int, float]:
    body = lib_symbol_block(lib_id)
    chunks = list(iter_pin_blocks(body))
    if not chunks:
        ext = re.search(r'\(extends\s+"([^"]+)"\)', body)
        if ext:
            parent = f"{lib_id.split(':')[0]}:{ext.group(1)}"
            return pin_def(parent, pin_num)
    for chunk in chunks:
        mnum = re.search(r'\(number\s+"([^"]+)"', chunk)
        mat = re.search(r"\(at\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\)", chunk)
        mlen = re.search(r"\(length\s+([-\d.]+)\)", chunk)
        if mnum and mat and mnum.group(1) == pin_num:
            length = float(mlen.group(1)) if mlen else 0.0
            return float(mat.group(1)), float(mat.group(2)), int(float(mat.group(3))), length
    raise KeyError(f"Pin {pin_num} not found in {lib_id}")


def pin_connection_local(px: float, py: float, prot: int, length: float) -> tuple[float, float]:
    """KiCad connects wires at the outer end of each pin graphic."""
    r = math.radians(prot)
    return px + length * math.cos(r), py - length * math.sin(r)


def pin_sheet(
    lib_id: str,
    sx: float,
    sy: float,
    srot: int,
    pin_num: str,
    *,
    outer: bool = False,
) -> tuple[float, float]:
    px, py, prot, length = pin_def(lib_id, pin_num)
    cx, cy = pin_connection_local(px, py, prot, length) if outer else (px, py)
    sr = math.radians(srot)
    rx = cx * math.cos(sr) - cy * math.sin(sr)
    ry = cx * math.sin(sr) + cy * math.cos(sr)
    return snap(sx + rx), snap(sy - ry)


def pin_pair(
    lib_id: str, sx: float, sy: float, srot: int, pin_num: str
) -> tuple[tuple[float, float], tuple[float, float]]:
    body = pin_sheet(lib_id, sx, sy, srot, pin_num, outer=False)
    tip = pin_sheet(lib_id, sx, sy, srot, pin_num, outer=True)
    return body, tip


def indent_block(text: str, tabs: int = 1) -> str:
    prefix = "\t" * tabs
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def prop(
    name: str,
    value: str,
    x: float,
    y: float,
    angle: int = 0,
    hide: bool = False,
    justify: str | None = None,
) -> str:
    fx = f"\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)"
    j = f"\n\t\t\t\t(justify {justify})" if justify else ""
    h = "\n\t\t\t\t(hide yes)" if hide else ""
    return f"""\t\t(property "{name}" "{value}"
\t\t\t(at {x} {y} {angle})
\t\t\t(effects{fx}{j}{h}
\t\t\t)
\t\t)"""


def place(
    lib_id: str,
    ref: str,
    value: str,
    x: float,
    y: float,
    rot: int = 0,
    footprint: str = "",
    exclude_sim: bool | None = None,
) -> str:
    body = lib_symbol_block(lib_id)
    nums: list[str] = []
    for chunk in iter_pin_blocks(body):
        mnum = re.search(r'\(number\s+"([^"]+)"', chunk)
        if mnum:
            nums.append(mnum.group(1))
    if not nums:
        ext = re.search(r'\(extends\s+"([^"]+)"\)', body)
        if ext:
            parent = f"{lib_id.split(':')[0]}:{ext.group(1)}"
            for chunk in iter_pin_blocks(lib_symbol_block(parent)):
                mnum = re.search(r'\(number\s+"([^"]+)"', chunk)
                if mnum:
                    nums.append(mnum.group(1))
    seen: set[str] = set()
    pin_lines = []
    for num in nums:
        if num in seen:
            continue
        seen.add(num)
        pin_lines.append(f'\t\t(pin "{num}"\n\t\t\t(uuid "{uid()}")\n\t\t)')
    if exclude_sim is None:
        exclude_sim = lib_id.startswith("power:")
    sim = "yes" if exclude_sim else "no"
    return f"""\t(symbol
\t\t(lib_id "{lib_id}")
\t\t(at {snap(x)} {snap(y)} {rot})
\t\t(unit 1)
\t\t(exclude_from_sim {sim})
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "{uid()}")
{prop("Reference", ref, x + 2.54, y - 2.54)}
{prop("Value", value, x + 2.54, y + 2.54)}
{prop("Footprint", footprint, x, y, hide=True)}
{prop("Datasheet", "~", x, y, hide=True)}
{prop("Description", f"Placed {ref}", x, y, hide=True)}
{chr(10).join(pin_lines)}
\t\t(instances
\t\t\t(project "{PROJECT}"
\t\t\t\t(path "/{ROOT_UUID}"
\t\t\t\t\t(reference "{ref}")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)"""


class Sch:
    def __init__(self) -> None:
        self.wires: list[str] = []
        self.labels: list[str] = []
        self.junctions: list[tuple[float, float]] = []
        self.no_connects: list[str] = []
        self.symbols: list[str] = []
        self.texts: list[str] = []

    def wire(self, x1: float, y1: float, x2: float, y2: float) -> None:
        x1, y1, x2, y2 = snap(x1), snap(y1), snap(x2), snap(y2)
        self.wires.append(
            f"""\t(wire
\t\t(pts
\t\t\t(xy {x1} {y1})
\t\t\t(xy {x2} {y2})
\t\t)
\t\t(stroke
\t\t\t(width 0)
\t\t\t(type default)
\t\t)
\t\t(uuid "{uid()}")
\t)"""
        )

    def route(self, points: list[tuple[float, float]]) -> None:
        for a, b in zip(points, points[1:]):
            if a != b:
                self.wire(*a, *b)

    def pin_connect(self, lib_id: str, sx: float, sy: float, srot: int, pin_num: str) -> tuple[float, float]:
        """Route a short stub along the pin graphic; return the electrical connection point."""
        body, tip = pin_pair(lib_id, sx, sy, srot, pin_num)
        if body != tip:
            self.wire(*body, *tip)
        return tip

    def label(self, name: str, x: float, y: float, angle: int = 0) -> None:
        x, y = snap(x), snap(y)
        self.labels.append(
            f"""\t(label "{name}"
\t\t(at {x} {y} {angle})
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t)
\t\t\t(justify left bottom)
\t\t)
\t\t(uuid "{uid()}")
\t)"""
        )

    def junction(self, x: float, y: float) -> None:
        pt = (snap(x), snap(y))
        if pt not in self.junctions:
            self.junctions.append(pt)

    def no_connect(self, x: float, y: float) -> None:
        x, y = snap(x), snap(y)
        self.no_connects.append(
            f"""\t(no_connect
\t\t(at {x} {y})
\t\t(uuid "{uid()}")
\t)"""
        )

    def text(self, s: str, x: float, y: float, size: float = 2.54) -> None:
        self.texts.append(
            f"""\t(text "{s}"
\t\t(exclude_from_sim no)
\t\t(at {snap(x)} {snap(y)} 0)
\t\t(effects
\t\t\t(font
\t\t\t\t(size {size} {size})
\t\t\t)
\t\t\t(justify left bottom)
\t\t)
\t\t(uuid "{uid()}")
\t)"""
        )

    def add(self, block: str) -> None:
        self.symbols.append(block)

    def junction_items(self) -> list[str]:
        return [
            f"""\t(junction
\t\t(at {x} {y})
\t\t(diameter 0)
\t\t(color 0 0 0 0)
\t\t(uuid "{uid()}")
\t)"""
            for x, y in self.junctions
        ]


def build() -> str:
    sch = Sch()

    # Layout constants
    col_x0 = 210.0
    col_dx = 25.4
    led_y0 = 155.0
    led_dy = 15.24
    col_bus_y = snap(130.0)
    row_bus_x = snap(85.0)
    gnd_rail_y = snap(45.0)
    matrix_right_x = snap(col_x0 + 7 * col_dx)  # right edge of LED matrix (for docs)

    # --- Arduino header (J1): D2/D3 row bases, D10-D12 shift register, +5V, GND ---
    ar_x, ar_y = sym_pos(40.0, 80.0)
    sch.add(
        place(
            "Connector_Generic:Conn_01x07",
            "J1",
            "Arduino_Uno",
            ar_x,
            ar_y,
            footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical",
        )
    )

    def ar_pin(sig: str) -> tuple[float, float]:
        return pin_sheet("Connector_Generic:Conn_01x07", ar_x, ar_y, 0, AR_PINS[sig])

    # --- 74HC595 column driver (Q1-Q3 -> 220R -> column anodes) ---
    u_x, u_y = sym_pos(170.0, 60.0)
    sch.add(place("74xx:74HC595", "U1", "74HC595", u_x, u_y, footprint="Package_DIP:DIP-16_W7.62mm"))

    def u_pin(num: str) -> tuple[float, float]:
        return pin_sheet("74xx:74HC595", u_x, u_y, 0, num)

    # --- Power ---
    p5_x, p5_y = sym_pos(120.0, 40.0)
    gnd_x, gnd_y = sym_pos(130.0, gnd_rail_y)
    flg5_x, flg5_y = sym_pos(p5_x + 5.08, p5_y)
    flg_g_x, flg_g_y = sym_pos(gnd_x + 5.08, gnd_y)
    sch.add(place("power:+5V", "#PWR01", "+5V", p5_x, p5_y))
    sch.add(place("power:GND", "#PWR02", "GND", gnd_x, gnd_y))
    sch.add(place("power:PWR_FLAG", "#FLG01", "PWR_FLAG", flg5_x, flg5_y))
    sch.add(place("power:PWR_FLAG", "#FLG02", "PWR_FLAG", flg_g_x, flg_g_y))

    p5 = pin_sheet("power:+5V", p5_x, p5_y, 0, "1")
    gnd = pin_sheet("power:GND", gnd_x, gnd_y, 0, "1")
    pwr_flag = pin_sheet("power:PWR_FLAG", flg5_x, flg5_y, 0, "1")
    gnd_flag = pin_sheet("power:PWR_FLAG", flg_g_x, flg_g_y, 0, "1")

    for pt in (u_pin("16"), u_pin("10"), ar_pin("+5V")):
        sch.route([pt, p5])
    sch.junction(*p5)
    sch.route([p5, pwr_flag])

    for pt in (u_pin("8"), u_pin("13")):
        sch.route([pt, gnd])
    ar_gnd = ar_pin("GND")
    sch.route([ar_gnd, (gnd[0], ar_gnd[1]), gnd])
    sch.junction(*gnd)
    sch.route([gnd, gnd_flag])

    # Unused shift-register outputs get no-connect flags (SRCLR/OE handled by power rails)
    for up in UNUSED_Q_PINS:
        sch.no_connect(*pin_sheet("74xx:74HC595", u_x, u_y, 0, up, outer=False))

    # Shift register control: D10->SER(DATA), D11->RCLK(LATCH), D12->SRCLK(CLK)
    for sig, upin, net, mid_x in SHIFT_SIGS:
        a = ar_pin(sig)
        u = u_pin(upin)
        sch.route([a, (mid_x, a[1]), (mid_x, u[1]), u])
        sch.junction(mid_x, u[1])
        sch.label(net, mid_x, u[1])

    # Column resistors R1-R3: 74HC595 Q1-Q3 -> 220R -> C1-C3 column buses.
    # Vertical resistor: top pin to the shift-register output, bottom pin to the
    # column bus, so the resistor sits IN SERIES (never short both leads together).
    for i, qp in enumerate(COL_Q_PINS):
        cx, ry = sym_pos(col_x0 + i * col_dx, 95.0)
        sch.add(
            place(
                "Device:R",
                f"R{i + 1}",
                "220",
                cx,
                ry,
                rot=0,
                footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P5.08mm_Vertical",
            )
        )
        r_top = pin_sheet("Device:R", cx, ry, 0, "1")
        r_bot = pin_sheet("Device:R", cx, ry, 0, "2")
        qout = u_pin(qp)
        sch.route([qout, (cx, qout[1]), r_top])
        sch.route([r_bot, (cx, col_bus_y)])
        sch.junction(cx, col_bus_y)
        sch.label(f"C{i + 1}", cx, col_bus_y)

    # LED matrix (NUM_ROWS x NUM_COLS) — anodes on column buses, cathodes on row buses
    row_ys: list[float] = []
    for row in range(NUM_ROWS):
        row_y = sym_pos(col_x0, led_y0 + row * led_dy)[1]
        row_ys.append(row_y)
        row_bus_y = pin_sheet("Device:LED", sym_pos(col_x0, row_y)[0], row_y, 0, "1", outer=True)[1]

        for col in range(NUM_COLS):
            led_num = row * NUM_COLS + col + 1
            lx, ly = sym_pos(col_x0 + col * col_dx, row_y)
            sch.add(
                place(
                    "Device:LED",
                    f"LED{led_num}",
                    "LED",
                    lx,
                    ly,
                    footprint="LED_THT:LED_D5.0mm",
                )
            )
            k = sch.pin_connect("Device:LED", lx, ly, 0, "1")
            a = pin_sheet("Device:LED", lx, ly, 0, "2")
            col_x = sym_pos(col_x0 + col * col_dx, col_bus_y)[0]
            sch.route([a, (col_x, col_bus_y)])
            sch.junction(col_x, col_bus_y)
            sch.route([k, (row_bus_x, k[1]), (row_bus_x, row_bus_y)])
            sch.junction(row_bus_x, row_bus_y)

        sch.label(f"ROW{row + 1}", row_bus_x, row_bus_y)

    # Row drivers: D2/D3 -> 1kR -> NPN base; collector -> ROW bus; emitter -> GND
    q_x = snap(55.0)

    for row in range(NUM_ROWS):
        row_y = row_ys[row]
        row_bus_y = pin_sheet("Device:LED", sym_pos(col_x0, row_y)[0], row_y, 0, "1", outer=True)[1]

        rx, ry = sym_pos(40.0 + row * 5.0, row_y)
        sch.add(
            place(
                "Device:R",
                f"R{NUM_COLS + 1 + row}",
                "1k",
                rx,
                ry,
                rot=0,
                footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P5.08mm_Vertical",
            )
        )
        r_top = pin_sheet("Device:R", rx, ry, 0, "1")
        r_bot = pin_sheet("Device:R", rx, ry, 0, "2")

        tx, ty = sym_pos(q_x, row_y)
        sch.add(
            place(
                "Transistor_BJT:Q_NPN_EBC",
                f"Q{row + 1}",
                "2N2222",
                tx,
                ty,
                footprint="Package_TO_SOT_THT:TO-92_Inline",
            )
        )
        # Q_NPN_EBC: 1=E, 2=B, 3=C — emitter extends DOWN, collector UP.
        e = sch.pin_connect("Transistor_BJT:Q_NPN_EBC", tx, ty, 0, "1")
        b = pin_sheet("Transistor_BJT:Q_NPN_EBC", tx, ty, 0, "2")
        c = pin_sheet("Transistor_BJT:Q_NPN_EBC", tx, ty, 0, "3")

        ap = ar_pin(ROW_SIGS[row])
        # Arduino D2/D3 -> top of 1k -> (through resistor) -> bottom -> transistor base.
        sch.route([ap, (rx, ap[1]), r_top])
        sch.route([r_bot, (rx, b[1]), b])
        sch.junction(*b)
        # Collector -> row cathode bus (routes up/right, away from the emitter).
        sch.route([c, (row_bus_x, c[1]), (row_bus_x, row_bus_y)])
        sch.junction(row_bus_x, row_bus_y)
        # Emitter -> its own GND symbol placed directly below (no crossing wires).
        eg_x, eg_y = sym_pos(e[0], row_y + 15.0)
        sch.add(place("power:GND", f"#PWR0{row + 3}", "GND", eg_x, eg_y))
        eg = pin_sheet("power:GND", eg_x, eg_y, 0, "1")
        sch.route([e, (e[0], eg[1]), eg])

    # Documentation
    sch.text("Music Visualizer - 2x3 LED Matrix", 25, 15, 3.81)
    sch.text("Laptop plays MP3, streams volume level (0-6) over serial", 25, 21, 1.78)
    sch.text("Arduino multiplexes 2 rows @ ~250Hz; 74HC595 drives 3 columns", 25, 27, 1.78)
    sch.text("J1: D2/D3=row bases  D10=DATA  D11=LATCH  D12=CLK  +5V/GND", 25, 33, 1.78)

    lib_symbols = "\n".join(indent_block(lib_symbol_block(lib_id), 2) for lib_id in LIB_MAP)

    return f"""(kicad_sch
\t(version 20250114)
\t(generator "cursor-schematic-gen")
\t(generator_version "5.0")
\t(uuid "{ROOT_UUID}")
\t(paper "A3")
\t(title_block
\t\t(title "LED Matrix Music Visualizer (2x3)")
\t\t(date "2026-07-07")
\t\t(rev "2.0")
\t\t(comment 1 "6-LED (2 row x 3 col) matrix music visualizer")
\t\t(comment 2 "Laptop streams volume level 0-6 over serial")
\t\t(comment 3 "74HC595 drives 3 columns (Q1-Q3); 2x 2N2222 row sinks")
\t\t(comment 4 "D10 DATA | D11 LATCH | D12 CLK | D2/D3 row bases")
\t)
\t(lib_symbols
{lib_symbols}
\t)
{chr(10).join(sch.junction_items())}
{chr(10).join(sch.no_connects)}
{chr(10).join(sch.wires)}
{chr(10).join(sch.labels)}
{chr(10).join(sch.texts)}
{chr(10).join(sch.symbols)}
\t(sheet_instances
\t\t(path "/{ROOT_UUID}"
\t\t\t(page "1")
\t\t)
\t)
\t(embedded_fonts no)
)
"""


def run_erc_report(sch_path: Path, report_path: Path) -> tuple[int, int]:
    if not KICAD_CLI.exists():
        print("KiCad CLI not found — skipping ERC", file=sys.stderr)
        return 0, 0
    result = subprocess.run(
        [str(KICAD_CLI), "sch", "erc", str(sch_path), "--format", "report", "--output", str(report_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and not report_path.exists():
        print(result.stderr or result.stdout, file=sys.stderr)
        return -1, -1
    text = report_path.read_text() if report_path.exists() else ""
    errors = warnings = 0
    for line in text.splitlines():
        if "Errors" in line and "Warnings" in line:
            m = re.search(r"Errors (\d+).*Warnings (\d+)", line)
            if m:
                errors, warnings = int(m.group(1)), int(m.group(2))
    return errors, warnings


def verify_netlist(sch_path: Path) -> bool:
    if not KICAD_CLI.exists():
        return True
    net_path = sch_path.with_suffix(".net")
    subprocess.run(
        [str(KICAD_CLI), "sch", "export", "netlist", str(sch_path), "--output", str(net_path)],
        capture_output=True,
    )
    if not net_path.exists():
        return False
    text = net_path.read_text()
    # (ref, pin, expected net) — verifies the wiring matches README / music_visualizer.ino
    checks = [
        ("J1", "3", "/DATA"),    # Arduino D10 -> 74HC595 SER
        ("J1", "4", "/LATCH"),   # Arduino D11 -> 74HC595 RCLK
        ("J1", "5", "/CLK"),     # Arduino D12 -> 74HC595 SRCLK
        ("U1", "14", "/DATA"),   # SER
        ("U1", "12", "/LATCH"),  # RCLK
        ("U1", "11", "/CLK"),    # SRCLK
        ("Q1", "3", "/ROW1"),    # Q1 collector -> row 1 cathodes
        ("Q2", "3", "/ROW2"),    # Q2 collector -> row 2 cathodes
        ("Q1", "1", "GND"),      # emitter -> GND
        ("Q2", "1", "GND"),
        # Column resistors bridge each Q output to its column bus (LED anodes)
        ("R1", "2", "/C1"),      # R1 -> column 1
        ("R2", "2", "/C2"),      # R2 -> column 2
        ("R3", "2", "/C3"),      # R3 -> column 3
        # LED matrix: anode -> column bus, cathode -> row bus
        ("LED1", "2", "/C1"), ("LED1", "1", "/ROW1"),
        ("LED2", "2", "/C2"), ("LED2", "1", "/ROW1"),
        ("LED3", "2", "/C3"), ("LED3", "1", "/ROW1"),
        ("LED4", "2", "/C1"), ("LED4", "1", "/ROW2"),
        ("LED5", "2", "/C2"), ("LED5", "1", "/ROW2"),
        ("LED6", "2", "/C3"), ("LED6", "1", "/ROW2"),
        ("U1", "16", "+5V"),     # VCC
        ("U1", "10", "+5V"),     # SRCLR held high
        ("U1", "8", "GND"),      # GND
        ("U1", "13", "GND"),     # OE held low
    ]
    blocks = re.split(r"\n\t\t\(net", text)[1:]
    node_re = re.compile(r'\(node\s+\(ref "([^"]+)"\)\s*\(pin "([^"]+)"')

    def net_of(ref: str, pin: str) -> str | None:
        for b in blocks:
            name = re.search(r'\(name "([^"]*)"\)', b)
            for r, p in node_re.findall(b):
                if r == ref and p == pin:
                    return name.group(1) if name else None
        return None

    for ref, pin, expect in checks:
        actual = net_of(ref, pin)
        if actual != expect:
            print(f"Netlist check failed: {ref} pin {pin} on {actual!r}, expected {expect!r}", file=sys.stderr)
            return False

    # Each column resistor's input pin must share the corresponding 74HC595 Q output net.
    pairs = [(("R1", "1"), ("U1", "1")), (("R2", "1"), ("U1", "2")), (("R3", "1"), ("U1", "3"))]
    # Each base resistor's input pin must share the Arduino row-drive net.
    pairs += [(("R4", "1"), ("J1", "1")), (("R5", "1"), ("J1", "2"))]
    # Each base resistor's output pin must reach the matching transistor base.
    pairs += [(("R4", "2"), ("Q1", "2")), (("R5", "2"), ("Q2", "2"))]
    for (ra, pa), (rb, pb) in pairs:
        na, nb = net_of(ra, pa), net_of(rb, pb)
        if na is None or na != nb:
            print(f"Netlist check failed: {ra}.{pa} ({na!r}) not tied to {rb}.{pb} ({nb!r})", file=sys.stderr)
            return False

    # Every resistor must be in series: its two pins must be on DIFFERENT nets.
    refs = sorted(set(re.findall(r'\(ref "(R\d+)"\)', text)))
    for r in refs:
        n1, n2 = net_of(r, "1"), net_of(r, "2")
        if n1 is None or n2 is None or n1 == n2:
            print(f"Netlist check failed: resistor {r} is shorted (pin1={n1!r}, pin2={n2!r})", file=sys.stderr)
            return False
    return True


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"Wrote {OUT}")
    ok = verify_netlist(OUT)
    print(f"Netlist checks: {'PASS' if ok else 'FAIL'}")
    errors, warnings = run_erc_report(OUT, ERC_RPT)
    print(f"ERC: {errors} errors, {warnings} warnings -> {ERC_RPT}")
    if errors > 0 or not ok:
        sys.exit(1)
