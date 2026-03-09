# lcc_extract_eventids.py

Parses one or more Tower LCC / Tower LCC+Q CDI backup (`.txt`) files and produces CSV files ready for JMRI import.

Handles both file formats:

- **Quoted-Printable encoded** (`=3D` separators, `=0A=` line endings)
- **Plain text** (standard `=` separators)

## Usage

```bash
python3 lcc_extract_eventids.py <backup1.txt> [backup2.txt ...]
python3 .lcc_extract_eventids.py *.txt
```

## Filter Options

| Option | Description |
|--------|-------------|
| `--all` | Include all allocated event ID slots, even unused ones. Default: unused slots (action/trigger = 0) are skipped. |
| `--type <TYPE>` | Only output events inferred as a specific JMRI type: `Sensor`, `Turnout`, `Signal`, `Light`. Repeatable. |
| `--section <SECTION>` | Only output events from a specific CDI section: `Port I/O`, `Track Receivers`, `Track Transmitters`, `System`. Repeatable. |
| `--node <NODE_ID>` | Only output events from a specific node ID, e.g. `02.01.57.40.00.08`. Repeatable. |
| `--out <dir>` | Output directory. Default: current directory. |

## Examples

```bash
# Sensors and turnouts only, used events only (default):
python3 lcc_extract_eventids.py *.txt --type Sensor --type Turnout

# Everything including unused slots (for full audit):
python3 lcc_extract_eventids.py *.txt --all

# Track circuits only:
python3 lcc_extract_eventids.py *.txt --section "Track Transmitters" --section "Track Receivers"

# Single node, signals only:
python3 lcc_extract_eventids.py *.txt --node 02.01.57.40.00.08 --type Signal
```
