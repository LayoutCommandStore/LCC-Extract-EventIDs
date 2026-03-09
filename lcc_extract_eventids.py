#!/usr/bin/env python3
"""
lcc_extract_eventids.py  —  LCC Node Backup → JMRI CSV Converter
==============================================================
Parses one or more Tower LCC / Tower LCC+Q CDI backup (.txt) files and
produces CSV files ready for JMRI import.

Handles both file formats:
  • Quoted-Printable encoded  (=3D separators, =0A= line endings)
  • Plain text                (standard = separators)

Usage:
  python3 lcc_extract_eventids.py <backup1.txt> [backup2.txt ...]
  python3 lcc_extract_eventids.py *.txt

Filter options:
  --used-only           Only output events with a non-zero action/trigger
                        (skips unassigned slots). DEFAULT: enabled.
  --all                 Include all allocated event ID slots, even unused ones.
  --type Sensor         Only output events inferred as a specific JMRI type.
                        Valid values: Sensor, Turnout, Signal, Light
                        Can be repeated: --type Sensor --type Turnout
  --section "Port I/O"  Only output events from a specific CDI section.
                        Valid values: "Port I/O", "Track Receivers",
                                      "Track Transmitters", "System"
                        Can be repeated.
  --node 02.01.57...    Only output events from a specific node ID.
                        Can be repeated.
  --out <dir>           Output directory (default: current directory)

Examples:
  # Sensors and turnouts only, used events only (default):
  python3 lcc_extract_eventids.py *.txt --type Sensor --type Turnout

  # Everything including unused slots (for full audit):
  python3 lcc_extract_eventids.py *.txt --all

  # Track circuits only:
  python3 lcc_extract_eventids.py *.txt --section "Track Transmitters" --section "Track Receivers"

  # Single node, signals only:
  python3 lcc_extract_eventids.py *.txt --node 02.01.57.40.00.08 --type Signal
"""

import re
import csv
import os
import sys
import argparse

# ── Event ID regex ────────────────────────────────────────────────────────────
EID_RE = re.compile(r'^[0-9A-Fa-f]{2}(\.[0-9A-Fa-f]{2}){7}$')

# ── Consumer Action codes ─────────────────────────────────────────────────────
CONSUMER_ACTION = {
    '0': 'Unused',
    '1': 'Set Active (turn ON)',
    '2': 'Set Inactive (turn OFF)',
    '3': 'Pulse',
    '4': 'Toggle',
    '5': 'Set Active (no change if already)',
    '6': 'Set Inactive (no change if already)',
}

# ── Producer trigger codes ────────────────────────────────────────────────────
PRODUCER_TRIGGER = {
    '0': 'Unused',
    '1': 'Line output goes active',
    '2': 'Line output goes inactive',
    '3': 'Pulse start',
    '4': 'Pulse end',
    '5': 'Input goes active (HIGH)',
    '6': 'Input goes inactive (LOW)',
    '7': 'Timeout / delayed trigger',
}

# ── JMRI type heuristics ──────────────────────────────────────────────────────
# Note: "approach" deliberately excluded from Signal keywords to avoid false
# matches with MSS block names like "West Appr", "MSS-Approach", etc.
TURNOUT_KEYWORDS  = ('turnout','switch','throw','close','divert','points')
SIGNAL_KEYWORDS   = ('signal','aspect','head','mast','stop','clear',
                     'diverge','restricting','tx circuit','track circuit',
                     'track transmit','track receiv')
SENSOR_KEYWORDS   = ('sensor','occupan','detect','block','optic','mss',
                     'local','appr','approach','advancedapproach','advappr',
                     'input','button','contact','presence','rx circuit')
LIGHT_KEYWORDS    = ('light','led','lamp','illumin','output','indicator')

VALID_TYPES    = {'Sensor', 'Turnout', 'Signal', 'Light'}
VALID_SECTIONS = {'Port I/O', 'Track Receivers', 'Track Transmitters', 'System'}

def infer_jmri_type(text):
    t = text.lower()
    if any(k in t for k in TURNOUT_KEYWORDS): return 'Turnout'
    if any(k in t for k in SIGNAL_KEYWORDS):  return 'Signal'
    if any(k in t for k in SENSOR_KEYWORDS):  return 'Sensor'
    if any(k in t for k in LIGHT_KEYWORDS):   return 'Light'
    return 'Sensor'


# ── File parsing ──────────────────────────────────────────────────────────────

def is_qp(content):
    return '=3D' in content

def parse_qp(content):
    pairs = {}
    raw = content.replace('\r\n', '\n').replace('\r', '\n')
    logical_lines = []
    buf = ''
    for phys_line in raw.split('\n'):
        if phys_line.endswith('=') and not phys_line.endswith('=0A='):
            buf += phys_line[:-1]
        else:
            buf += phys_line
            logical_lines.append(buf)
            buf = ''
    for line in logical_lines:
        line = line.strip()
        if not line:
            continue
        if line.endswith('=0A='): line = line[:-4]
        elif line.endswith('=0A'): line = line[:-3]
        if '=3D' in line:
            key, _, val = line.partition('=3D')
            pairs[key.strip()] = val.strip()
    return pairs

def parse_plain(content):
    pairs = {}
    for raw_line in content.split('\n'):
        line = raw_line.rstrip('\r').strip()
        if not line:
            continue
        if '=' in line:
            key, _, val = line.partition('=')
            pairs[key.strip()] = val.strip()
    return pairs

def parse_backup(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    node_id_filename = extract_node_id_from_filename(filepath)
    kv = parse_qp(content) if is_qp(content) else parse_plain(content)
    return node_id_filename, kv

def extract_node_id_from_filename(filepath):
    base = os.path.basename(filepath)
    m = re.match(
        r'config_([0-9a-fA-F]{2})_([0-9a-fA-F]{2})_([0-9a-fA-F]{2})'
        r'_([0-9a-fA-F]{2})_([0-9a-fA-F]{2})_([0-9a-fA-F]{2})',
        base, re.IGNORECASE)
    if m:
        return '.'.join(g.upper() for g in m.groups())
    return None

def is_event_id(val):
    return bool(EID_RE.match(val.strip()))

def norm_eid(val):
    return val.strip().upper()


# ── Event extraction ──────────────────────────────────────────────────────────

def extract_events(node_id_file, kv, used_only=True):
    rows = []
    skipped_unused = 0

    node_name = kv.get(
        'NODE ID.Your name and description for this node.Node Name', '').strip()
    node_desc = kv.get(
        'NODE ID.Your name and description for this node.Node Description', '').strip()

    node_id = node_id_file
    if not node_id:
        for v in kv.values():
            if is_event_id(v):
                node_id = norm_eid(v)[:23]
                break

    def add(section, detail, event_id, label, role, action_detail, is_used):
        combo = f"{detail} — {label}" if detail else label
        jtype = infer_jmri_type(f"{section} {detail} {label}")
        rows.append({
            'NodeID':       node_id or '',
            'NodeName':     node_name,
            'NodeDesc':     node_desc,
            'Section':      section,
            'Detail':       detail,
            'EventID':      norm_eid(event_id),
            'EventLabel':   label,
            'EventName':    f"{node_name} | {combo}" if node_name else combo,
            'Role':         role,
            'ActionDetail': action_detail,
            'JMRIType':     jtype,
            'InUse':        'Yes' if is_used else 'No',
        })

    # ── Port I/O Lines ────────────────────────────────────────────────────────
    line_indices = set()
    for key in kv:
        m = re.match(r'Port I/O\.Line\((\d+)\)\.', key)
        if m:
            line_indices.add(int(m.group(1)))

    for idx in sorted(line_indices):
        pfx = f'Port I/O.Line({idx}).'
        line_desc  = kv.get(pfx + 'Line Description', '').strip()
        line_label = line_desc if line_desc else f'Line {idx}'

        consumer_indices = set()
        for key in kv:
            m = re.match(rf'Port I/O\.Line\({idx}\)\.Commands/Consumers\((\d+)\)\.Command', key)
            if m:
                consumer_indices.add(int(m.group(1)))

        for cidx in sorted(consumer_indices):
            cpfx   = f'{pfx}Commands/Consumers({cidx}).'
            cmd    = kv.get(cpfx + 'Command', '').strip()
            action = kv.get(cpfx + 'Action',  '0').strip()
            if not is_event_id(cmd):
                continue
            is_used = (action != '0')
            if used_only and not is_used:
                skipped_unused += 1
                continue
            action_label = CONSUMER_ACTION.get(action, f'Action {action}')
            add('Port I/O', line_label, cmd,
                f'Consumer {cidx}: {action_label}', 'Consumer', action_label, is_used)

        producer_indices = set()
        for key in kv:
            m = re.match(rf'Port I/O\.Line\({idx}\)\.Actions/Producers\((\d+)\)\.Indicator', key)
            if m:
                producer_indices.add(int(m.group(1)))

        for pidx in sorted(producer_indices):
            ppfx    = f'{pfx}Actions/Producers({pidx}).'
            ind     = kv.get(ppfx + 'Indicator',        '').strip()
            trigger = kv.get(ppfx + 'Upon this action', '0').strip()
            if not is_event_id(ind):
                continue
            is_used = (trigger != '0')
            if used_only and not is_used:
                skipped_unused += 1
                continue
            trigger_label = PRODUCER_TRIGGER.get(trigger, f'Trigger {trigger}')
            add('Port I/O', line_label, ind,
                f'Producer {pidx}: {trigger_label}', 'Producer', trigger_label, is_used)

    # ── Track Receivers ───────────────────────────────────────────────────────
    rx_indices = set()
    for key in kv:
        m = re.match(r'Track Receivers\.Rx Circuit\((\d+)\)\.Link Address', key)
        if m:
            rx_indices.add(int(m.group(1)))

    for idx in sorted(rx_indices):
        pfx  = f'Track Receivers.Rx Circuit({idx}).'
        desc = kv.get(pfx + 'Remote Mast Description', '').strip()
        addr = kv.get(pfx + 'Link Address', '').strip()
        if not is_event_id(addr):
            continue
        is_used = bool(desc)
        if used_only and not is_used:
            skipped_unused += 1
            continue
        detail = desc if desc else f'Rx Circuit {idx}'
        add('Track Receivers', detail, addr, 'Track Circuit Rx Link',
            'Consumer/Producer', 'MSS Track Circuit Receiver', is_used)

    # ── Track Transmitters ────────────────────────────────────────────────────
    tx_indices = set()
    for key in kv:
        m = re.match(r'Track Transmitters\.Tx Circuit\((\d+)\)\.Link Address', key)
        if m:
            tx_indices.add(int(m.group(1)))

    for idx in sorted(tx_indices):
        pfx  = f'Track Transmitters.Tx Circuit({idx}).'
        desc = kv.get(pfx + 'Track Circuit Description', '').strip()
        addr = kv.get(pfx + 'Link Address', '').strip()
        if not is_event_id(addr):
            continue
        is_used = bool(desc)
        if used_only and not is_used:
            skipped_unused += 1
            continue
        detail = desc if desc else f'Tx Circuit {idx}'
        add('Track Transmitters', detail, addr, 'Track Circuit Tx Link',
            'Producer', 'MSS Track Circuit Transmitter', is_used)

    # ── System events ─────────────────────────────────────────────────────────
    for suffix, label in [
        ('Syntax Messages.Syntax Events.Build Successful', 'STL Compile: Build Successful'),
        ('Syntax Messages.Syntax Events.Syntax Error(s)',  'STL Compile: Syntax Error'),
    ]:
        val = kv.get(suffix, '').strip()
        if is_event_id(val):
            add('System', 'Compile Status', val, label, 'Producer', 'System event', True)

    return rows, skipped_unused


# ── Post-extraction filtering ─────────────────────────────────────────────────

def apply_filters(rows, type_filter=None, section_filter=None, node_filter=None):
    filtered = rows
    if node_filter:
        nf = {n.upper() for n in node_filter}
        filtered = [r for r in filtered if r['NodeID'].upper() in nf]
    if section_filter:
        sf = {s.lower() for s in section_filter}
        filtered = [r for r in filtered if r['Section'].lower() in sf]
    if type_filter:
        tf = {t.lower() for t in type_filter}
        filtered = [r for r in filtered if r['JMRIType'].lower() in tf]
    return filtered


# ── Output writers ────────────────────────────────────────────────────────────

FULL_HEADERS = [
    'EventID', 'EventName', 'NodeID', 'NodeName', 'NodeDesc',
    'Section', 'Detail', 'EventLabel', 'Role', 'ActionDetail', 'JMRIType', 'InUse',
]

def write_full_csv(rows, outpath):
    with open(outpath, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FULL_HEADERS, extrasaction='ignore')
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"  → {outpath}  ({len(rows)} events)")

def write_jmri_names_csv(rows, outpath):
    with open(outpath, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['EventID', 'EventName'])
        seen = set()
        for row in rows:
            eid = row['EventID']
            if eid not in seen:
                w.writerow([eid, row['EventName']])
                seen.add(eid)
    print(f"  → {outpath}  ({len(set(r['EventID'] for r in rows))} unique event IDs)")

def write_nodes_csv(rows, outpath):
    seen = {}
    for r in rows:
        nid = r['NodeID']
        if nid not in seen:
            seen[nid] = {'NodeID': nid, 'NodeName': r['NodeName'],
                         'NodeDesc': r['NodeDesc'], 'EventCount': 0}
        seen[nid]['EventCount'] += 1
    with open(outpath, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['NodeID','NodeName','NodeDesc','EventCount'])
        w.writeheader()
        for row in seen.values():
            w.writerow(row)
    print(f"  → {outpath}  ({len(seen)} nodes)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        description='Convert LCC node backup files to JMRI-ready CSV files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument('files', nargs='+', metavar='backup.txt')
    p.add_argument('--all', dest='include_unused', action='store_true',
                   help='Include unused/unassigned event ID slots')
    p.add_argument('--type', dest='type_filter', action='append', metavar='TYPE',
                   help='Filter by JMRI type: Sensor, Turnout, Signal, Light (repeatable)')
    p.add_argument('--section', dest='section_filter', action='append', metavar='SECTION',
                   help='Filter by section: "Port I/O", "Track Receivers", '
                        '"Track Transmitters", "System" (repeatable)')
    p.add_argument('--node', dest='node_filter', action='append', metavar='NODE_ID',
                   help='Filter by node ID e.g. 02.01.57.40.00.08 (repeatable)')
    p.add_argument('--out', dest='outdir', default='.',
                   help='Output directory (default: current directory)')
    return p

def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.type_filter:
        bad = [t for t in args.type_filter if t not in VALID_TYPES]
        if bad:
            parser.error(f"Unknown --type value(s): {bad}. Valid: {sorted(VALID_TYPES)}")
    if args.section_filter:
        bad = [s for s in args.section_filter if s not in VALID_SECTIONS]
        if bad:
            parser.error(f"Unknown --section value(s): {bad}. Valid: {sorted(VALID_SECTIONS)}")

    os.makedirs(args.outdir, exist_ok=True)
    used_only = not args.include_unused

    all_rows = []
    total_skipped = 0

    for filepath in args.files:
        if not os.path.exists(filepath):
            print(f"  [WARN] File not found: {filepath}")
            continue
        print(f"Parsing: {os.path.basename(filepath)}")
        node_id_file, kv = parse_backup(filepath)
        rows, skipped = extract_events(node_id_file, kv, used_only=used_only)
        total_skipped += skipped
        all_rows.extend(rows)

        node_name = rows[0]['NodeName'] if rows else '(unknown)'
        node_id   = rows[0]['NodeID']   if rows else node_id_file or '?'
        consumers = sum(1 for r in rows if 'Consumer' in r['Role'])
        producers = sum(1 for r in rows if r['Role'] == 'Producer')
        skip_note = f"  |  Skipped unused: {skipped}" if skipped else ""
        print(f"  Node: {node_id}  ({node_name})")
        print(f"  Extracted: {len(rows)} events  ({consumers} consumers, {producers} producers){skip_note}")

    if not all_rows:
        print("No events found.")
        sys.exit(1)

    filtered_rows = apply_filters(
        all_rows,
        type_filter=args.type_filter,
        section_filter=args.section_filter,
        node_filter=args.node_filter,
    )

    print(f"\n--- Summary ---")
    print(f"Total after unused filter : {len(all_rows)}"
          + (f"  (skipped {total_skipped} unused slots)" if total_skipped else ""))
    if len(filtered_rows) != len(all_rows):
        print(f"After --type/--section/--node filters: {len(filtered_rows)}")

    if not filtered_rows:
        print("No events match the specified filters.")
        sys.exit(0)

    # Build output filename suffix from active filters
    parts = []
    if args.type_filter:
        parts.append('_'.join(sorted(t.lower() for t in args.type_filter)))
    if args.section_filter:
        parts.append('_'.join(s.replace(' ','').lower() for s in sorted(args.section_filter)))
    if args.include_unused:
        parts.append('all')
    suffix = ('_' + '_'.join(parts)) if parts else ''

    def out(name): return os.path.join(args.outdir, name)

    print(f"\nWriting output files...")
    write_full_csv(filtered_rows,       out(f"lcc_events{suffix}.csv"))
    write_jmri_names_csv(filtered_rows, out(f"lcc_jmri_names{suffix}.csv"))
    write_nodes_csv(filtered_rows,      out(f"lcc_nodes{suffix}.csv"))

    print(f"\nDone.")
    if not any([args.type_filter, args.section_filter, args.include_unused]):
        print("Tip: use --type Sensor --type Turnout to focus on I/O events.")
        print("     use --all to include all allocated slots for a full audit.")

if __name__ == '__main__':
    main()
