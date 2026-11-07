#!/usr/bin/env python3
"""Core library for reading/writing FC 26 FBCHUNKS/T3DB squad and career files."""

import re
import struct
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path
from copy import deepcopy

DB_HEADER = b"\x44\x42\x00\x08\x00\x00\x00\x00"
FBCHUNKS = b"\x46\x42\x43\x48\x55\x4e\x4b\x53\x01\x00"


def _make_fifa_crc_table():
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
        table.append(c)
    return table


_FIFA_CRC_TABLE = _make_fifa_crc_table()


def fifa_crc(data):
    """CRC-32/MPEG-2 (poly 0x04C11DB7, MSB first, init 0xFFFFFFFF, no final xor) used by T3DB."""
    c = 0xFFFFFFFF
    t = _FIFA_CRC_TABLE
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ t[(c >> 24) ^ b]
    return c


def load_meta(path):
    tree = ET.parse(path)
    root = tree.getroot()
    table_names = {}
    field_names = {}
    field_range = {}
    field_depth = {}
    field_type = {}
    for table in root.findall("./table"):
        t_name = table.get("name")
        t_short = table.get("shortname")
        table_names[t_short] = t_name
        for field in table.findall("./fields/field"):
            f_name = field.get("name")
            f_short = field.get("shortname")
            f_type = field.get("type")
            f_range = field.get("rangelow", "0")
            f_depth = field.get("depth", "0")
            field_names[f_short] = f_name
            field_type[f_short] = f_type
            field_depth[f_short] = int(f_depth)
            if f_type == "DBOFIELDTYPE_INTEGER":
                field_range[t_name + f_name] = int(f_range)
            else:
                field_range[t_name + f_name] = 0
    return table_names, field_names, field_range, field_depth, field_type


def read_nullbyte_str(data, pos, length):
    end = data.find(b"\x00", pos)
    if end < 0 or end > pos + length:
        end = pos + length
    return data[pos:end].decode("utf-8", errors="ignore")


def read_bits(data, pos, bit_offset, bit_depth):
    byte_off = pos + (bit_offset >> 3)
    start_bit = bit_offset & 7
    value = 0
    bits_read = 0
    while bits_read < bit_depth:
        if byte_off >= len(data):
            break
        b = data[byte_off]
        available = 8 - start_bit
        to_read = min(available, bit_depth - bits_read)
        mask = (1 << to_read) - 1
        value |= ((b >> start_bit) & mask) << bits_read
        bits_read += to_read
        start_bit = 0
        byte_off += 1
    return value


def write_bits(data, pos, bit_offset, bit_depth, value):
    value -= 0  # caller passes raw value without range adjustment
    byte_off = pos + (bit_offset >> 3)
    start_bit = bit_offset & 7
    bits_written = 0
    while bits_written < bit_depth:
        if byte_off >= len(data):
            break
        available = 8 - start_bit
        to_write = min(available, bit_depth - bits_written)
        mask = (1 << to_write) - 1
        bit_chunk = (value >> bits_written) & mask
        data[byte_off] &= ~(mask << start_bit)
        data[byte_off] |= bit_chunk << start_bit
        bits_written += to_write
        start_bit = 0
        byte_off += 1


class SquadFile:
    def __init__(self, path, meta_path):
        self.path = Path(path)
        self.meta_path = Path(meta_path)
        self.table_names, self.field_names, self.field_range, self.field_depth, self.field_type = load_meta(meta_path)
        self.raw = bytearray(self.path.read_bytes())
        self.db_offset = self.raw.find(DB_HEADER)
        if self.db_offset < 0:
            raise ValueError("T3DB header not found in squad file")
        self.db_size = struct.unpack_from("<I", self.raw, self.db_offset + 8)[0]
        self.db_data = self.raw[self.db_offset:self.db_offset + self.db_size]
        self.tables_meta = self._read_table_index()
        self._records = {}
        self._table_fields = {}
        self._dirty = set()

    def _read_table_index(self):
        tc = struct.unpack_from("<I", self.db_data, 16)[0]
        off = 24
        meta = []
        for _ in range(tc):
            short = self.db_data[off:off+4].decode("latin-1", errors="replace")
            offset = struct.unpack_from("<I", self.db_data, off+4)[0]
            name = self.table_names.get(short)
            meta.append({"short": short, "offset": offset, "name": name})
            off += 8
        off += 4
        self._tables_start = off
        return meta

    def _parse_table(self, table_name):
        if table_name in self._records:
            return self._records[table_name], self._table_fields[table_name]

        entry = next((m for m in self.tables_meta if m["name"] == table_name), None)
        if entry is None:
            raise KeyError(f"Table {table_name} not found")

        pos = self._tables_start + entry["offset"]
        pos += 4  # skip
        record_size = struct.unpack_from("<I", self.db_data, pos)[0]
        pos += 4
        pos += 10  # skip
        valid_records = struct.unpack_from("<H", self.db_data, pos)[0]
        pos += 2
        pos += 4  # skip
        fields_count = self.db_data[pos]
        pos += 1
        pos += 11  # skip

        fields = []
        for _ in range(fields_count):
            f_type = struct.unpack_from("<I", self.db_data, pos)[0]
            f_bit_offset = struct.unpack_from("<I", self.db_data, pos+4)[0]
            f_short = self.db_data[pos+8:pos+12].decode("latin-1", errors="replace")
            f_bit_depth = struct.unpack_from("<I", self.db_data, pos+12)[0]
            pos += 16
            f_name = self.field_names.get(f_short, f_short)
            range_low = self.field_range.get(table_name + f_name, 0)
            fields.append({
                "type": f_type,
                "bit_offset": f_bit_offset,
                "short": f_short,
                "name": f_name,
                "bit_depth": f_bit_depth,
                "range_low": range_low,
            })
        fields.sort(key=lambda f: f["bit_offset"])

        records = []
        data_start = pos
        for rec_idx in range(valid_records):
            rec_pos = data_start + rec_idx * record_size
            record = {"__rec_pos": rec_pos, "__record_size": record_size}
            for fld in fields:
                f_type = fld["type"]
                f_name = fld["name"]
                if f_type == 0:  # string
                    byte_off = rec_pos + (fld["bit_offset"] >> 3)
                    value = read_nullbyte_str(self.db_data, byte_off, fld["bit_depth"] >> 3)
                elif f_type == 3:  # int
                    value = read_bits(self.db_data, rec_pos, fld["bit_offset"], fld["bit_depth"])
                    value += fld["range_low"]
                elif f_type == 4:  # float
                    byte_off = rec_pos + (fld["bit_offset"] >> 3)
                    value = struct.unpack_from("<f", self.db_data, byte_off)[0]
                else:
                    value = None
                record[f_name] = value
            records.append(record)

        self._records[table_name] = records
        self._table_fields[table_name] = fields
        return records, fields

    def get_table(self, table_name):
        records, _ = self._parse_table(table_name)
        # Return shallow copies without internal markers
        return [{k: v for k, v in r.items() if not k.startswith("__")} for r in records]

    def update_field(self, table_name, rec_idx, field_name, new_value):
        records, fields = self._parse_table(table_name)
        record = records[rec_idx]
        rec_pos = record["__rec_pos"]
        fld = next((f for f in fields if f["name"] == field_name), None)
        if fld is None:
            raise KeyError(f"Field {field_name} not found in {table_name}")

        if fld["type"] == 3:  # int
            write_bits(self.db_data, rec_pos, fld["bit_offset"], fld["bit_depth"], new_value - fld["range_low"])
            record[field_name] = new_value
            self._dirty.add(table_name)
        else:
            raise NotImplementedError("Only integer fields can be updated currently")

    def _table_header(self, table_name):
        """(header pos, record_size, n_records allocated, valid_records) of a table.
        Header: +4 record size, +16 allocated slots (u16), +18 valid records (u16).
        The record area always holds n_records * record_size bytes; slots after the valid
        ones are unused. The game rejects a table whose byte size does not match n_records."""
        entry = next((m for m in self.tables_meta if m["name"] == table_name), None)
        if entry is None:
            raise KeyError(f"Table {table_name} not found")
        pos = self._tables_start + entry["offset"]
        record_size = struct.unpack_from("<I", self.db_data, pos + 4)[0]
        n_records, valid = struct.unpack_from("<HH", self.db_data, pos + 16)
        return pos, record_size, n_records, valid

    def delete_record(self, table_name, rec_idx):
        """Remove a record IN PLACE (swap-remove): the last valid record moves into the freed
        slot, its old slot is zeroed and valid_records decremented. The file size never changes
        and every other record keeps its index (playerloans keeps a row index at header +22 that
        the game follows, so rows must not be shifted). Note: row order is not preserved."""
        records, fields = self._parse_table(table_name)
        if rec_idx < 0 or rec_idx >= len(records):
            raise IndexError(f"rec_idx {rec_idx} out of range for {table_name}")
        pos, record_size, n_records, valid = self._table_header(table_name)
        data_start = self._field_start(table_name)
        last = valid - 1
        rec_pos = data_start + rec_idx * record_size
        last_pos = data_start + last * record_size
        if rec_idx != last:
            self.db_data[rec_pos:rec_pos + record_size] = self.db_data[last_pos:last_pos + record_size]
            if struct.unpack_from("<H", self.db_data, pos + 22)[0] == last:
                struct.pack_into("<H", self.db_data, pos + 22, rec_idx)
        self.db_data[last_pos:last_pos + record_size] = bytes(record_size)
        struct.pack_into("<H", self.db_data, pos + 18, valid - 1)
        self._records.pop(table_name, None)
        self._table_fields.pop(table_name, None)
        self._dirty.add(table_name)

    def _invalidate_all(self):
        self._records.clear()
        self._table_fields.clear()

    def insert_record(self, table_name, values=None):
        """Append a zeroed record in the first unused slot (IN PLACE, file size unchanged).
        Returns the record index; use update_field to set fields."""
        pos, record_size, n_records, valid = self._table_header(table_name)
        if valid >= n_records:
            raise ValueError(f"{table_name} is full ({n_records} slots); delete a row first")
        slot = self._field_start(table_name) + valid * record_size
        self.db_data[slot:slot + record_size] = bytes(record_size)
        struct.pack_into("<H", self.db_data, pos + 18, valid + 1)
        self._records.pop(table_name, None)
        self._table_fields.pop(table_name, None)
        self._dirty.add(table_name)
        return valid  # index of the newly inserted record

    def _n_fields_start(self, table_name):
        """Position where the record data region begins for a table."""
        entry = next((m for m in self.tables_meta if m["name"] == table_name), None)
        pos = self._tables_start + entry["offset"]
        pos += 4
        record_size = struct.unpack_from("<I", self.db_data, pos)[0]
        pos += 4
        pos += 10
        pos += 2
        pos += 4
        fields_count = self.db_data[pos]
        pos += 1
        pos += 11
        for _ in range(fields_count):
            pos += 16
        return pos

    def _field_start(self, table_name):
        return self._n_fields_start(table_name)

    def save(self, output_path=None, display_name=None):
        """Write modified db_data back into the FBCHUNKS wrapper and recompute every checksum
        the game verifies (otherwise FC 26 reports the squad file as corrupted):
        - T3DB: header CRC (bytes 0..20 at 20), table index CRC (at 24 + 8*n), per table a
          header CRC (bytes 0..32 at 32) and a body CRC (fields..end-4 at the table's last 4 bytes),
          all CRC-32/MPEG-2 (fifa_crc).
        - FBCHUNKS: size fields at 14 and 1174, and the zlib CRC32 of raw[1174:] stored right
          after the SaveType_* string.
        display_name optionally replaces the name shown in the game's Load list (offset 18)."""
        if output_path is None:
            output_path = self.path
        db = self.db_data
        struct.pack_into("<I", db, 8, len(db))
        struct.pack_into("<I", db, 20, fifa_crc(db[:20]))
        tc = struct.unpack_from("<I", db, 16)[0]
        idx_end = 24 + 8 * tc
        struct.pack_into("<I", db, idx_end, fifa_crc(db[24:idx_end]))
        starts = sorted(struct.unpack_from("<I", db, 28 + 8 * i)[0] + idx_end + 4 for i in range(tc))
        for i, p in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(db)
            struct.pack_into("<I", db, p + 32, fifa_crc(db[p:p + 32]))
            struct.pack_into("<I", db, end - 4, fifa_crc(db[p + 36:end - 4]))
        self.raw[self.db_offset:self.db_offset + self.db_size] = db
        self.db_size = len(db)
        if display_name is not None:
            name = display_name.encode("utf-8")[:31]  # rest of the 32-byte field zero-filled
            self.raw[18:50] = name.ljust(32, b"\x00")
        struct.pack_into("<I", self.raw, 14, len(self.raw) - 1126)
        m = re.search(rb"SaveType_[A-Za-z]+\x00", bytes(self.raw[1126:1180]))
        save_type = m.group(0) if m else b"SaveType_Squads\x00"
        crc_pos = 1126 + len(save_type)
        body = crc_pos + 32  # 1174 for squads: size of the rest, then the chunk data
        struct.pack_into("<I", self.raw, body, len(self.raw) - body - 4)
        struct.pack_into("<I", self.raw, crc_pos, zlib.crc32(bytes(self.raw[body:])))
        Path(output_path).write_bytes(self.raw)

    def player_clubs(self):
        players_records, _ = self._parse_table("players")
        teams_records, _ = self._parse_table("teams")
        links_records, _ = self._parse_table("teamplayerlinks")
        dc_records, _ = self._parse_table("dcplayernames")

        # dcplayernames field names may vary; inspect first record
        if dc_records:
            sample = dc_records[0]
            name_id_key = next((k for k in sample if "id" in k.lower() and "name" not in k.lower()), None)
            name_key = next((k for k in sample if "name" in k.lower()), None)
        else:
            name_id_key = name_key = None
        names = {}
        if name_id_key and name_key:
            for r in dc_records:
                names[r[name_id_key]] = r[name_key]

        teams = {t.get("teamid"): t for t in teams_records}
        player_map = {p.get("playerid"): p for p in players_records}

        result = []
        for link in links_records:
            pid = link.get("playerid")
            tid = link.get("teamid")
            player = player_map.get(pid, {})
            team = teams.get(tid, {})
            firstname = names.get(player.get("firstnameid", 0), "")
            lastname = names.get(player.get("lastnameid", 0), "")
            common = names.get(player.get("commonnameid", 0), "")
            display = common or f"{firstname} {lastname}".strip()
            result.append({
                "playerid": pid,
                "firstname": firstname,
                "lastname": lastname,
                "commonname": common,
                "displayname": display,
                "teamid": tid,
                "teamname": team.get("teamname", ""),
                "teamabbreviation": team.get("teamabbreviation", ""),
            })
        return result


def find_squad_files(directory="."):
    return [str(p) for p in Path(directory).glob("Squads*") if p.is_file()]


def detect_save_type(path):
    """Return 'career', 'squad' or None based on the FBCHUNKS SaveType string."""
    try:
        raw = Path(path).read_bytes()[:2048]
    except OSError:
        return None
    if not raw.startswith(FBCHUNKS):
        return None
    m = re.search(rb"SaveType_([A-Za-z]+)", raw)
    if not m:
        return None
    kind = m.group(1).decode("ascii", "ignore").lower()
    return "career" if kind.startswith("career") else "squad" if kind == "squads" else kind


def find_career_files(directory="."):
    """Career-mode save files (CmMgrC*, ...), like find_squad_files."""
    return [str(p) for p in Path(directory).glob("CmMgr*") if p.is_file()]


def career_overview(sq):
    """Human-readable summary of the active career (career_users, manager, calendar, contracts).
    Never raises on tables absent in the file."""
    info = {}

    def row(table, fields):
        try:
            records, _ = sq._parse_table(table)
        except KeyError:
            return None
        if not records:
            return None
        return {f: records[0].get(f) for f in fields if f in records[0]}

    user = row("career_users", ["firstname", "commonname", "surname", "clubteamid",
                                 "nationalteamid", "leagueid", "primarycompobjid",
                                 "usertype", "wage", "seasoncount", "sponsorid"])
    if user:
        info["user"] = user
        info["mode"] = "player" if user.get("usertype") == 1 else "manager"
        first = user.get("commonname") or (user.get("firstname") or "") + " " + (user.get("surname") or "")
        info["user_display"] = first.strip() or "Player"

    mgr = row("career_manager", ["firstnameid", "surnameid", "personid", "nationalityid",
                                  "teamid", "reputation", "isuser"])
    if mgr:
        info["manager"] = mgr

    cal = row("career_calendar", ["day", "month", "year", "season", "date"])
    if cal:
        info["calendar"] = cal

    try:
        contracts, _ = sq._parse_table("career_playercontract")
    except KeyError:
        contracts = []
    info["contracts"] = len(contracts)

    return info
