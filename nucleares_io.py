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

def read_save_file(file_path):
    """Loads the master XML file from disk."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    tree = ET.parse(file_path)
    return tree, tree.getroot()

def write_save_file(tree, output_path):
    """Writes the master XML file back to disk with the correct encoding."""
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

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
