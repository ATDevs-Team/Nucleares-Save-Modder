'''
    Nucleares Mod Tool XML Read/Write Library
    Copyright (C) 2026  ATDevs Team

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
'''
import xml.etree.ElementTree as ET
import os
import shutil
import sqlite3

def read_save_file(file_path):
    """Loads the master XML file from disk."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    tree = ET.parse(file_path)
    return tree, tree.getroot()

def write_save_file(tree, output_path):
    """Writes the master XML file back to disk with the correct encoding."""
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

# ─────────────────────────────────────────────────────────────────────────────
#  Companion .sqlite statistics database
#
#  Every savegame_025_*.xml the game itself writes ships alongside a
#  savegame_025_*.xml.sqlite holding a single "Estadisticas" table (a
#  historical log that feeds the in-game trend graphs). If that file is
#  missing or lacks the table when a save is loaded, the game logs a
#  SqliteException + ArgumentNullException on every load and does NOT
#  repair the file itself (confirmed against a real Player.log) -- so any
#  save NSM writes needs a valid companion database, or the player will see
#  those errors forever afterward.
# ─────────────────────────────────────────────────────────────────────────────

STATS_DB_SCHEMA = (
    "CREATE TABLE Estadisticas ("
    "Dia INTEGER, Hora INTEGER, Minuto INTEGER, Tipo INTEGER, Valor REAL)"
)

def stats_db_path(xml_path):
    """Path of the .sqlite companion database for a given save .xml path."""
    return xml_path + ".sqlite"

def _is_valid_stats_db(path):
    """True if *path* is a readable SQLite file containing the Estadisticas table."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    try:
        con = sqlite3.connect(path)
        try:
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='Estadisticas'"
            )
            return cur.fetchone() is not None
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False

def write_empty_stats_db(path):
    """Create a fresh, empty-but-valid statistics database at *path*."""
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    try:
        con.execute(STATS_DB_SCHEMA)
        con.commit()
    finally:
        con.close()

def ensure_stats_db(source_xml_path, dest_xml_path):
    """
    Make sure *dest_xml_path* has a valid companion .sqlite.

    If *source_xml_path*'s companion database exists and is valid, it is
    copied byte-for-byte (preserving the save's historical graph data).
    Otherwise a fresh, empty-but-schema-correct database is created so the
    game never has to fall back to its own (non-self-healing) error path.

    Returns "copied", "created", or "unchanged" (dest already valid and no
    usable source to copy from -- shouldn't normally happen, but keeps this
    idempotent).
    """
    dest_db = stats_db_path(dest_xml_path)
    source_db = stats_db_path(source_xml_path) if source_xml_path else None

    if source_db and os.path.abspath(source_db) == os.path.abspath(dest_db):
        return "unchanged" if _is_valid_stats_db(dest_db) else _create_or_repair(dest_db)

    if source_db and _is_valid_stats_db(source_db):
        shutil.copyfile(source_db, dest_db)
        return "copied"

    return _create_or_repair(dest_db)

def _create_or_repair(dest_db):
    if _is_valid_stats_db(dest_db):
        return "unchanged"
    write_empty_stats_db(dest_db)
    return "created"

def decode_payload(xml_string):
    """Takes a raw string from the save and parses it into an XML Element."""
    clean_str = xml_string.strip()
    # Python's parser fails if it reads utf-16 in the declaration but the string is utf-8
    if 'encoding="utf-16"' in clean_str:
        clean_str = clean_str.replace('encoding="utf-16"', 'encoding="utf-8"')
    try:
        return ET.fromstring(clean_str)
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse inner XML payload: {e}")

def encode_payload(element):
    """Converts an XML Element back to a string with the game's expected header."""
    xml_bytes = ET.tostring(element, encoding="utf-8")
    xml_string = xml_bytes.decode("utf-8")
    return '<?xml version="1.0" encoding="utf-16"?>\n' + xml_string
