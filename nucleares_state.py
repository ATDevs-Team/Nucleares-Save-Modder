'''
    Nucleares Mod Tool Save Parser/Modder Library
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
import html
import os
import re
import glob

from nucleares_io import decode_payload, encode_payload


def _html_decode(text):
    """HTML-unescape a component payload, then parse as XML."""
    clean = html.unescape(text.strip())
    if 'encoding="utf-16"' in clean:
        clean = clean.replace('encoding="utf-16"', 'encoding="utf-8"')
    return ET.fromstring(clean)


# ─────────────────────────────────────────────────────────────────────────────
#  NSM Database  (.nsmdb file reader / merger)
# ─────────────────────────────────────────────────────────────────────────────

class NSMDatabase:
    """
    Loads and merges ``correct_safe_values-<NUM>.nsmdb`` files found in a
    data directory.

    File format
    -----------
    Lines beginning with ``#`` are comments; blank lines are ignored.

    Block syntax::

        =BEGIN=COMPONENT_TAG=
        ==OVERWRITE_LOWER_DATABASES==:==true==BOOLEAN==
        ==SomeFloat==:==123.45==FLOAT==
        ==SomeInt==:==10==INT==
        ==SomeBool==:==false==BOOLEAN==
        ==SomeText==:=="hello world"==TEXT==
        =END=COMPONENT_TAG=

    *COMPONENT_TAG* should match the in-save XML element name of a reactor
    component (e.g. ``NUCLEO``, ``PRESURIZADOR``, ``EVAPORADOR``).

    Two special pseudo-tags drive generic repair / flood operations and do
    **not** need a matching XML element:

    ``REPAIR_DEFAULTS``
        Override the target values used by ``repair_all_objects``.
        Recognised keys: ``Integridad``, ``desgaste``, ``porcentaje_roto``,
        ``temperatura``.

    ``FLUID_DEFAULTS``
        Override the fill amounts used by ``flood_reserves``.
        Recognised keys: ``AGUA_Cantidad``, ``BORO_Cantidad``.

    Merge order
    -----------
    Files are sorted by their numeric suffix in **ascending** order so that a
    higher-numbered file is applied *on top of* lower-numbered ones.

    Per-block, the ``OVERWRITE_LOWER_DATABASES`` flag controls the merge
    strategy:

    * ``true``  — replace all previously loaded entries for that TAG entirely.
    * ``false`` (default) — merge; keys from the higher-numbered file win on
      collision, but keys absent from it are kept from lower files.

    The ``OVERWRITE_LOWER_DATABASES`` directive itself is **never** stored as a
    data key.

    Supported value types
    ---------------------
    ==============  ===================================================
    ``INT``         Parsed with ``int()``.
    ``FLOAT``       Parsed with ``float()``.
    ``BOOLEAN``     ``true`` / ``1`` / ``yes`` → ``True``, else ``False``.
    ``TEXT``        String; surrounding double-quotes are stripped.
    ==============  ===================================================
    """

    # Matches:  ==KEY==:==VALUE==TYPE==
    # VALUE may be a bare non-whitespace token or a double-quoted string.
    _LINE_RE  = re.compile(
        r'^==(.+?)==:==("(?:[^"]*)"|\S+?)==(INT|FLOAT|TEXT|BOOLEAN)==$'
    )
    _BEGIN_RE = re.compile(r'^=BEGIN=(.+)=$')
    _END_RE   = re.compile(r'^=END=(.+)=$')

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = data_dir
        # _db[tag][key] = (python_value, type_str)
        self._db:           dict[str, dict[str, tuple]] = {}
        self._loaded_files: list[str] = []
        self._errors:       list[str] = []
        self._load_all()

    # ── public API ──

    def get(self, tag: str, key: str, default=None):
        """Return the stored Python value for *(tag, key)*, or *default*."""
        entry = self._db.get(tag, {}).get(key)
        return entry[0] if entry is not None else default

    def get_as_str(self, tag: str, key: str, default: str | None = None) -> str | None:
        """
        Return the value as an XML-ready text string.

        Booleans are emitted as lowercase ``"true"`` / ``"false"`` (the format
        the Nucleares save uses).  All other types are passed through
        ``str()``.
        """
        entry = self._db.get(tag, {}).get(key)
        if entry is None:
            return default
        value, type_str = entry
        if type_str == "BOOLEAN":
            return "true" if value else "false"
        return str(value)

    def tag_keys(self, tag: str) -> dict[str, tuple]:
        """Return a copy of ``{key: (python_value, type_str)}`` for *tag*."""
        return dict(self._db.get(tag, {}))

    def has_tag(self, tag: str) -> bool:
        """Return ``True`` if *tag* has any entries in the database."""
        return tag in self._db

    @property
    def loaded_files(self) -> list[str]:
        """Paths of all .nsmdb files that were successfully read."""
        return list(self._loaded_files)

    @property
    def errors(self) -> list[str]:
        """Non-fatal parse warnings / errors accumulated during loading."""
        return list(self._errors)

    def summary(self) -> str:
        """One-line human-readable summary of the loaded database state."""
        n_files = len(self._loaded_files)
        n_tags  = len(self._db)
        n_keys  = sum(len(v) for v in self._db.values())
        err_txt = f", {len(self._errors)} warning(s)" if self._errors else ""
        return (
            f"NSMDatabase: {n_files} file(s) loaded, "
            f"{n_tags} tag(s), {n_keys} total key(s){err_txt}."
        )

    # ── internal: value parsing ──

    @staticmethod
    def _parse_typed_value(raw_value: str, type_str: str) -> tuple:
        """Convert *raw_value* (a string token from the file) to a Python value."""
        type_str = type_str.upper()
        if type_str == "INT":
            return int(raw_value), "INT"
        if type_str == "FLOAT":
            return float(raw_value), "FLOAT"
        if type_str == "BOOLEAN":
            return raw_value.strip().lower() in ("true", "1", "yes"), "BOOLEAN"
        # TEXT — strip surrounding double-quotes when present
        if raw_value.startswith('"') and raw_value.endswith('"'):
            raw_value = raw_value[1:-1]
        return raw_value, "TEXT"

    # ── internal: file parsing ── 

    def _parse_file(self, filepath: str) -> dict[str, dict]:
        """
        Parse one .nsmdb file.

        Returns
        -------
        dict mapping tag → {"overwrite": bool, "entries": {key: (value, type_str)}}
        """
        result:            dict[str, dict]  = {}
        current_tag:       str | None       = None
        current_entries:   dict[str, tuple] = {}
        current_overwrite: bool             = False

        with open(filepath, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue

                begin_m = self._BEGIN_RE.match(line)
                end_m   = self._END_RE.match(line)

                if begin_m:
                    if current_tag is not None:
                        # An unclosed block — save what we have and warn
                        self._errors.append(
                            f"{filepath}:{lineno}: =BEGIN= encountered inside "
                            f"unclosed block '{current_tag}' — auto-closing it."
                        )
                        result[current_tag] = {
                            "overwrite": current_overwrite,
                            "entries":   current_entries,
                        }
                    current_tag       = begin_m.group(1)
                    current_entries   = {}
                    current_overwrite = False

                elif end_m:
                    closing = end_m.group(1)
                    if current_tag is None:
                        self._errors.append(
                            f"{filepath}:{lineno}: =END= without a matching "
                            f"=BEGIN= — skipped."
                        )
                    else:
                        if closing != current_tag:
                            self._errors.append(
                                f"{filepath}:{lineno}: =END={closing}= closes "
                                f"=BEGIN={current_tag}= (tag mismatch) — "
                                f"accepting anyway."
                            )
                        result[current_tag] = {
                            "overwrite": current_overwrite,
                            "entries":   current_entries,
                        }
                        current_tag       = None
                        current_entries   = {}
                        current_overwrite = False

                elif line.startswith("=="):
                    if current_tag is None:
                        self._errors.append(
                            f"{filepath}:{lineno}: entry found outside any "
                            f"block — skipped: {line!r}"
                        )
                        continue

                    m = self._LINE_RE.match(line)
                    if not m:
                        self._errors.append(
                            f"{filepath}:{lineno}: malformed entry — "
                            f"skipped: {line!r}"
                        )
                        continue

                    key, raw_val, type_str = m.group(1), m.group(2), m.group(3)

                    if key == "OVERWRITE_LOWER_DATABASES":
                        current_overwrite = (
                            raw_val.strip().lower() in ("true", "1", "yes")
                        )
                    else:
                        try:
                            current_entries[key] = self._parse_typed_value(
                                raw_val, type_str
                            )
                        except (ValueError, TypeError) as exc:
                            self._errors.append(
                                f"{filepath}:{lineno}: cannot parse "
                                f"{raw_val!r} as {type_str}: {exc} — skipped."
                            )

        # File ended with an open block
        if current_tag is not None:
            self._errors.append(
                f"{filepath}: file ended inside unclosed block "
                f"'{current_tag}' — saving partial data."
            )
            result[current_tag] = {
                "overwrite": current_overwrite,
                "entries":   current_entries,
            }

        return result

    # ── internal: load + merge ──

    def _load_all(self) -> None:
        """
        Discover all ``correct_safe_values-<NUM>.nsmdb`` files in *data_dir*,
        sort them ascending by NUM, and merge them into ``self._db``.
        """
        if not os.path.isdir(self.data_dir):
            return  # data directory does not exist — nothing to load

        candidates: list[tuple[int, str]] = []
        pattern = os.path.join(self.data_dir, "correct_safe_values-*.nsmdb")
        for path in glob.glob(pattern):
            m = re.search(
                r"correct_safe_values-(\d+)\.nsmdb$",
                os.path.basename(path),
            )
            if m:
                candidates.append((int(m.group(1)), path))

        candidates.sort()  # ascending: -1, -2, … -10, …

        for _num, path in candidates:
            try:
                parsed = self._parse_file(path)
            except OSError as exc:
                self._errors.append(f"Cannot open {path}: {exc}")
                continue

            for tag, data in parsed.items():
                if data["overwrite"] or tag not in self._db:
                    # Full replacement (or first time we see this TAG)
                    self._db[tag] = dict(data["entries"])
                else:
                    # Merge: higher-numbered file wins on key collision
                    self._db[tag].update(data["entries"])

            self._loaded_files.append(path)


# ─────────────────────────────────────────────────────────────────────────────
#  Save Memory Manager
# ─────────────────────────────────────────────────────────────────────────────

class SaveMemoryManager:
    def __init__(self, master_tree, master_root):
        self.master_tree = master_tree
        self.master_root = master_root

        self.state = {
            "player": {},
            "components": {},
            "fluid_network": None,        # Single decoded DIF element (mutable in-place)
            "fluid_network_node": None,   # The raw master XML node for write-back
            "objects": [],
            "switches": [],                # Type-C objetos entries: plain True/False literal, no XML payload
        }

        # Non-fatal problems hit while parsing (e.g. a single malformed
        # component payload) are collected here instead of raising, so one
        # bad blob doesn't take down the whole load. Surface to the user via
        # the GUI log.
        self.parse_warnings: list[str] = []

        # Load the .nsmdb database from ./data/ next to the script
        self._db = NSMDatabase(
            data_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        )

        self._parse_into_memory()

    # ── property: database summary ──

    @property
    def db_summary(self) -> str:
        """One-line summary of the loaded .nsmdb database state."""
        return self._db.summary()

    # ------------------------------------------------------------------
    # PARSING
    # ------------------------------------------------------------------

    def _parse_into_memory(self):
        """
        Builds the abstract in-memory state from the master XML tree.

        Every payload decode below is wrapped so a single malformed/unknown
        blob (a corrupted node, an unexpected save variant, etc.) is
        recorded in ``self.parse_warnings`` and skipped, rather than
        aborting the entire load — a save with one bad component should
        still let the rest of it be edited.
        """

        # 1. Player nodes (HTML-encoded XML blobs)
        for tag in ["JUGADOR", "LOGROS_MLIBRE"]:
            node = self.master_root.find(f".//{tag}")
            if node is not None and node.text:
                try:
                    self.state["player"][tag] = _html_decode(node.text)
                except (ValueError, ET.ParseError) as exc:
                    self.parse_warnings.append(f"Could not decode player node <{tag}>: {exc}")

        # 2. Reactor components (also HTML-encoded XML blobs inside <componentes>)
        comps_node = self.master_root.find(".//componentes")
        if comps_node is not None:
            for comp in comps_node:
                if comp.text and ("<?xml" in comp.text or "&lt;" in comp.text):
                    # HTML-encoded XML payload
                    try:
                        self.state["components"][comp.tag] = {
                            "type": "encoded",
                            "master_element": comp,
                            "inner_xml": _html_decode(comp.text),
                        }
                    except (ValueError, ET.ParseError) as exc:
                        self.parse_warnings.append(
                            f"Could not decode component <{comp.tag}>: {exc} "
                            f"— left untouched, will round-trip byte-for-byte."
                        )
                else:
                    self.state["components"][comp.tag] = {
                        "type": "direct",
                        "master_element": comp,
                        "inner_xml": comp,
                    }

        # 3. Fluid network — lives in <DISTRIBUCION_INTERNA_FLUIDOS>
        dif_node = self.master_root.find(".//DISTRIBUCION_INTERNA_FLUIDOS")
        if dif_node is not None and dif_node.text:
            try:
                self.state["fluid_network_node"] = dif_node
                self.state["fluid_network"] = _html_decode(dif_node.text)
            except (ValueError, ET.ParseError) as exc:
                self.state["fluid_network_node"] = None
                self.parse_warnings.append(f"Could not decode fluid network: {exc}")

        # 4. Objects — pipe-separated strings. Two shapes are mapped:
        #      - whose LAST segment is an HTML-escaped XML payload (fuel
        #        blocks, control rods, pumps, turbines, ...) -> state["objects"]
        #      - exactly 4 fields whose last field is a bare True/False
        #        literal (switches/levers, e.g. the PalancaSCRAM control-rod
        #        SCRAM levers) -> state["switches"]
        #    Pure scene-decoration objects (18 plain fields, no game state)
        #    are intentionally left unparsed — nothing to edit there.
        obj_node = self.master_root.find(".//objetos")
        if obj_node is not None:
            for obj in obj_node:
                if not (obj.text and "|" in obj.text):
                    continue
                parts = obj.text.split("|")
                last = parts[-1]

                if "<?xml" in last or "&lt;" in last:
                    try:
                        inner_xml = _html_decode(last)
                        self.state["objects"].append({
                            "master_element": obj,
                            "parts_prefix": parts[:-1],
                            "inner_xml": inner_xml,
                        })
                    except (ValueError, ET.ParseError) as exc:
                        self.parse_warnings.append(
                            f"Could not decode object payload for "
                            f"'{parts[0] if parts else obj.tag}': {exc}"
                        )
                    continue

                if len(parts) == 4 and last in ("True", "False"):
                    self.state["switches"].append({
                        "master_element": obj,
                        "parts": parts,   # mutable: parts[3] is the live True/False value
                        "id": parts[0],
                        "name": parts[1],
                        "class": parts[2],
                    })

    # ------------------------------------------------------------------
    # COMMIT
    # ------------------------------------------------------------------

    def commit_to_xml(self):
        """Writes the in-memory state back onto the master XML tree."""

        # Player
        for tag, inner_element in self.state["player"].items():
            node = self.master_root.find(f".//{tag}")
            if node is not None:
                node.text = encode_payload(inner_element)

        # Components
        for tag, comp_data in self.state["components"].items():
            if comp_data["type"] == "encoded":
                comp_data["master_element"].text = encode_payload(comp_data["inner_xml"])

        # Fluid network
        dif_node = self.state["fluid_network_node"]
        dif = self.state["fluid_network"]
        if dif_node is not None and dif is not None:
            dif_node.text = encode_payload(dif)

        # Objects
        for obj_data in self.state["objects"]:
            encoded_xml = encode_payload(obj_data["inner_xml"])
            full_string = "|".join(obj_data["parts_prefix"] + [encoded_xml])
            obj_data["master_element"].text = full_string

        # Switches/levers (plain True/False literal, no XML payload)
        for sw in self.state["switches"]:
            sw["master_element"].text = "|".join(sw["parts"])

    # ------------------------------------------------------------------
    # UTILITIES
    # ------------------------------------------------------------------

    def get_manual_map(self):
        mapping = {}
        for tag, elem in self.state["player"].items():
            mapping[f"[Player] {tag}"] = elem
        for tag, comp_data in self.state["components"].items():
            mapping[f"[Component] {tag}"] = comp_data["inner_xml"]
        dif = self.state["fluid_network"]
        if dif is not None:
            mapping["[Fluid] DISTRIBUCION_INTERNA_FLUIDOS"] = dif
        for obj in self.state["objects"]:
            obj_id = obj["parts_prefix"][0] if obj["parts_prefix"] else "unknown"
            mapping[f"[Object] {obj_id}"] = obj["inner_xml"]
        return mapping

    # ── switches / levers (type-C objetos entries) ──

    def list_switches(self, class_filter: str | None = None, id_prefix: str | None = None):
        """
        Return the ``state["switches"]`` entries, optionally filtered by
        object class (e.g. ``"PalancaMecanica"``) and/or by an id prefix
        (e.g. ``"PalancaSCRAM"`` to get every control-rod SCRAM lever).
        """
        result = self.state["switches"]
        if class_filter is not None:
            result = [s for s in result if s["class"] == class_filter]
        if id_prefix is not None:
            result = [s for s in result if s["id"].startswith(id_prefix)]
        return result

    def set_switch(self, switch_entry: dict, value: bool) -> None:
        """Set one switch (as returned by :meth:`list_switches`) to on/off."""
        switch_entry["parts"][3] = "True" if value else "False"

    def set_switches(self, class_filter: str | None = None, id_prefix: str | None = None,
                      value: bool = True) -> int:
        """Bulk-set every matching switch; returns how many were changed."""
        matches = self.list_switches(class_filter=class_filter, id_prefix=id_prefix)
        for sw in matches:
            self.set_switch(sw, value)
        return len(matches)

    # ── database helpers ──

    def _apply_db_tag(self, inner_xml: ET.Element, tag: str) -> int:
        """
        Apply every key/value stored in the database under *tag* to *inner_xml*
        via a direct ``find(".//KEY")`` element search — confirmed (by
        exhaustively indexing every field in 7 real saves, see
        SAVE_FORMAT.md) to be how every field in the current save format is
        actually stored. Returns the number of XML fields that were written.
        """
        if not self._db.has_tag(tag):
            return 0

        written = 0
        for key in self._db.tag_keys(tag):
            elem = inner_xml.find(f".//{key}")
            if elem is not None:
                elem.text = self._db.get_as_str(tag, key)
                written += 1

        return written

    # ------------------------------------------------------------------
    # MODIFICATION LOGIC (CHEATS)
    # ------------------------------------------------------------------

    def set_simple_stats(self, money, exp, level):
        money = min(max(float(money if money else 0), 0.0), 1_000_000_000.0)
        exp   = min(max(float(exp   if exp   else 0), 0.0), 1_000_000_000.0)
        level = min(max(int  (level if level else 1), 1), 100)

        # Money/XP/level live in LOGROS_MLIBRE in the current save format.
        # (JUGADOR holds physical player state -- health/position/radiation
        # -- not stats; a JUGADOR-side _valoresFloat dict write was removed
        # here because it never matched any real save, see SAVE_FORMAT.md §1.)
        logros = self.state["player"].get("LOGROS_MLIBRE")
        if logros is not None:
            for tag, val in [("Puntos", money), ("NuevoPuntos", money),
                             ("Experiencia", exp), ("Nivel", level)]:
                elem = logros.find(f".//{tag}")
                if elem is not None:
                    elem.text = str(val)
                else:
                    ET.SubElement(logros, tag).text = str(val)

        return f"Stats Applied: Money={money:,.0f}, Level={level}, EXP={exp:,.0f}"

    # ── repair_all_objects ──

    def repair_all_objects(self):
        """
        Repair every object, component, and fluid-network pipe in the save.

        Target values are taken from the ``REPAIR_DEFAULTS`` database tag when
        present, falling back to the built-in safe defaults shown below.

        REPAIR_DEFAULTS keys
        --------------------
        ``Integridad``    (FLOAT, default 100.0)
        ``temperatura``   (FLOAT, default 20.0)

        Note: earlier versions of this method also tried to reset
        ``desgaste``/``porcentaje_roto`` via a ``_valoresFloat`` dict lookup
        that (confirmed by exhaustively indexing every field across 7 real
        saves, see SAVE_FORMAT.md §1) never matches anything in the current
        save format — those two calls were silent no-ops. They've been
        replaced with resets of the real damage-indicator fields objects
        actually carry: ``_nivelDanoActual`` (damage level, healthy = "NADA"),
        ``IsDestruida`` (destroyed flag), ``SelloRoto`` (fuel block seal),
        and ``OxidoAcumulado`` (pump rust accumulation).
        """
        # ── resolve target values (DB → hardcoded fallback) ──
        tgt_integridad  = self._db.get("REPAIR_DEFAULTS", "Integridad", 100.0)
        tgt_temperatura = self._db.get("REPAIR_DEFAULTS", "temperatura", 20.0)

        tgt_integridad_str  = self._db.get_as_str("REPAIR_DEFAULTS", "Integridad",  str(tgt_integridad))
        tgt_temperatura_str = self._db.get_as_str("REPAIR_DEFAULTS", "temperatura", str(tgt_temperatura))

        count = 0

        # 1. Objects with XML payloads (fuel rods, pumps, turbines, valves, ...)
        for obj in self.state["objects"]:
            inner = obj["inner_xml"]
            repaired = False

            elem = inner.find(".//Integridad")
            if elem is not None and elem.text not in (tgt_integridad_str, str(int(tgt_integridad))):
                elem.text = tgt_integridad_str
                repaired = True

            elem = inner.find(".//Temperatura")
            if elem is not None and elem.text != tgt_temperatura_str:
                elem.text = tgt_temperatura_str
                repaired = True

            elem = inner.find(".//_nivelDanoActual")
            if elem is not None and elem.text != "NADA":
                elem.text = "NADA"
                repaired = True

            elem = inner.find(".//IsDestruida")
            if elem is not None and elem.text == "true":
                elem.text = "false"
                repaired = True

            elem = inner.find(".//SelloRoto")
            if elem is not None and elem.text == "true":
                elem.text = "false"
                repaired = True

            elem = inner.find(".//OxidoAcumulado")
            if elem is not None and elem.text != "0":
                elem.text = "0"
                repaired = True

            if repaired:
                count += 1

        # 2. Major reactor sub-systems (named components)
        for comp_name, comp_data in self.state["components"].items():
            inner = comp_data["inner_xml"]
            repaired = False

            for tag in ["Integridad", "IntegridadCalentadores", "IntegridadReliefTank"]:
                for elem in inner.findall(f".//{tag}"):
                    if elem.text not in (tgt_integridad_str, str(int(tgt_integridad))):
                        elem.text = tgt_integridad_str
                        repaired = True

            for tag in ["RequiereMantenimiento", "IsContaminado",
                        "DesactivadoPorFaltaDeSuministro",
                        "RequiereMantenimientoCalentadores"]:
                for elem in inner.findall(f".//{tag}"):
                    if elem.text == "true":
                        elem.text = "false"
                        repaired = True

            if repaired:
                count += 1

        # 3. Fluid-network pipes / containers
        dif = self.state["fluid_network"]
        if dif is not None:
            for elem in dif.findall(".//Integridad"):
                if elem.text not in (tgt_integridad_str, str(int(tgt_integridad))):
                    elem.text = tgt_integridad_str
                    count += 1

        return f"Fully repaired {count} reactor objects and systems."

    # ── scrub_core_poisons ──

    def scrub_core_poisons(self):
        """
        Zero out xenon / iodine poisoning in the reactor core.

        Target values are taken from the ``NUCLEO`` database tag when present,
        falling back to ``0.0`` / ``0`` / ``false`` as appropriate.

        NUCLEO keys honoured
        --------------------
        ``XenonConcentracion``, ``YodoConcentracion``,
        ``ReactividadXenon``, ``ReactividadYodo``             (FLOAT → default 0.0)
        ``_minutosAcumuladosEnvenenamientoXenon``,
        ``ContadorMasaCritica``                               (INT   → default 0)
        ``ExplosionInminente``, ``Flag_PerdioMasaCritica``,
        ``AlertaPorSituacionInsegura``                        (BOOLEAN → default false)
        """
        core_data = self.state["components"].get("NUCLEO")
        if not core_data:
            return "Failed: Could not find <NUCLEO> in components."
        core = core_data["inner_xml"]
        modified = []

        # Float fields (zeroed concentration / reactivity)
        for tag in ["XenonConcentracion", "YodoConcentracion",
                    "ReactividadXenon",   "ReactividadYodo"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = self._db.get_as_str("NUCLEO", tag, "0.0")
                modified.append(tag)

        # Integer counters
        for tag in ["_minutosAcumuladosEnvenenamientoXenon", "ContadorMasaCritica"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = self._db.get_as_str("NUCLEO", tag, "0")
                modified.append(tag)

        # Boolean alert / flag fields
        for tag in ["ExplosionInminente", "Flag_PerdioMasaCritica",
                    "AlertaPorSituacionInsegura"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = self._db.get_as_str("NUCLEO", tag, "false")
                modified.append(tag)

        # Apply any additional NUCLEO keys defined in the database that were
        # not already handled above (e.g. custom keys added in future .nsmdb
        # files).
        extra = self._apply_db_tag(core, "NUCLEO")

        return (
            f"Core Scrubbed. Modified: "
            f"{', '.join(modified) if modified else 'None'}"
            + (f" (+{extra} extra DB key(s))" if extra else "")
        )

    # ── normalize_pressures ── 

    def normalize_pressures(self):
        """
        Safely vent pressures to stable operating levels.

        Target pressures are taken from the database when present, falling back
        to the hard-coded safe defaults.

        Database keys consulted
        -----------------------
        ``PRESURIZADOR`` tag → ``PresionFisicaBAR``  (FLOAT, default 160.0)
        ``EVAPORADOR``   tag → ``PresionFisicaBAR``  (FLOAT, default  60.0)

        Any *additional* keys present under those tags in the database are also
        applied to the respective components.
        """
        pres_data = self.state["components"].get("PRESURIZADOR")
        evap_data = self.state["components"].get("EVAPORADOR")
        msgs = []

        if pres_data:
            pres = pres_data["inner_xml"]
            target = self._db.get_as_str("PRESURIZADOR", "PresionFisicaBAR", "160.0")
            elem = pres.find(".//PresionFisicaBAR")
            if elem is not None:
                elem.text = target
                msgs.append(f"Pressurizer → {target} BAR")
            # Apply any additional PRESURIZADOR keys from the DB
            self._apply_db_tag(pres, "PRESURIZADOR")

        if evap_data:
            evap = evap_data["inner_xml"]
            target = self._db.get_as_str("EVAPORADOR", "PresionFisicaBAR", "60.0")
            elem = evap.find(".//PresionFisicaBAR")
            if elem is not None:
                elem.text = target
                msgs.append(f"Steam generator → {target} BAR")
            # Apply any additional EVAPORADOR keys from the DB
            self._apply_db_tag(evap, "EVAPORADOR")

        if not msgs:
            return "normalize_pressures: no pressure elements found."
        return "Pressures normalized: " + ", ".join(msgs) + "."

    # ── max_backup_generators ── 

    def max_backup_generators(self):
        """
        Refuel and repair all backup diesel generators.

        Database keys consulted
        -----------------------
        ``CELECTROGENO`` tag:
          ``Combustible``          (FLOAT, default 99999.0)
          ``Integridad``           (FLOAT, default 100.0)
          ``IsContaminado``        (BOOLEAN, default false)
          ``RequiereMantenimiento``(BOOLEAN, default false)

        Any additional ``CELECTROGENO`` keys defined in the database are also
        applied to each generator element.
        """
        sum_data = self.state["components"].get("SUMINISTROINTERNO")
        if not sum_data:
            return "Failed: Could not find <SUMINISTROINTERNO>."
        suministro = sum_data["inner_xml"]

        # Resolve target values
        fuel_target  = self._db.get_as_str("CELECTROGENO", "Combustible",           "99999.0")
        integ_target = self._db.get_as_str("CELECTROGENO", "Integridad",             "100.0")
        contam_target= self._db.get_as_str("CELECTROGENO", "IsContaminado",          "false")
        maint_target = self._db.get_as_str("CELECTROGENO", "RequiereMantenimiento",  "false")

        electros = suministro.findall(".//CElectrogeno")
        for e in electros:
            c = e.find(".//Combustible")
            if c is not None:
                c.text = fuel_target

            for tag_name, target in [
                ("IsContaminado",         contam_target),
                ("RequiereMantenimiento", maint_target),
            ]:
                t = e.find(f".//{tag_name}")
                if t is not None:
                    t.text = target

            i = e.find(".//Integridad")
            if i is not None:
                i.text = integ_target

            # Apply any extra CELECTROGENO keys from the DB
            self._apply_db_tag(e, "CELECTROGENO")

        return (
            f"Generators Secured: Refueled {len(electros)} "
            f"backup diesel generator(s)."
        )

    # ── flood_reserves ──

    def flood_reserves(self):
        """
        Fill all coolant (AGUA) and boron (BORO) reserves in the fluid network.

        Fill amounts are taken from the database when present, falling back to
        the built-in safe defaults.

        FLUID_DEFAULTS keys
        -------------------
        ``AGUA_Cantidad``  (FLOAT, default 500 000.0)
        ``BORO_Cantidad``  (FLOAT, default 500 000.0)
        """
        dif = self.state["fluid_network"]
        if dif is None:
            return "Failed: Fluid network (DISTRIBUCION_INTERNA_FLUIDOS) not found."

        agua_target = self._db.get_as_str("FLUID_DEFAULTS", "AGUA_Cantidad", "500000.0")
        boro_target = self._db.get_as_str("FLUID_DEFAULTS", "BORO_Cantidad", "500000.0")

        agua_count = 0
        boro_count = 0

        for liquido in dif.findall(".//Liquido"):
            tipo = liquido.find("Tipo")
            cant = liquido.find("Cantidad")
            if tipo is None or cant is None:
                continue
            tipo_text = tipo.text.strip() if tipo.text else ""
            if tipo_text == "AGUA":
                cant.text = agua_target
                agua_count += 1
            elif tipo_text == "BORO":
                cant.text = boro_target
                boro_count += 1

        return (
            f"Fluids Flooded: filled {agua_count} water node(s) "
            f"and {boro_count} boron node(s)."
        )

    # ── apply_safe_values (generic DB cheat) ──

    def apply_safe_values(self, tag: str) -> str:
        """
        Apply **all** key/value pairs stored in the database under *tag* to
        every matching element in the save (component, player blob, or fluid
        network).

        This is a general-purpose cheat that can be triggered from the GUI via
        ``run_cheat("apply_safe_values:<TAG>")`` — the GUI helper
        ``run_cheat_tagged`` splits on ``:`` to pass the tag argument.

        Returns a human-readable result string suitable for the console log.
        """
        if not self._db.has_tag(tag):
            return f"apply_safe_values: no database entries found for tag '{tag}'."

        targets: list[str] = []

        # Named reactor component
        comp_data = self.state["components"].get(tag)
        if comp_data is not None:
            n = self._apply_db_tag(comp_data["inner_xml"], tag)
            targets.append(f"component '{tag}' ({n} field(s))")

        # Player blob
        player_elem = self.state["player"].get(tag)
        if player_elem is not None:
            n = self._apply_db_tag(player_elem, tag)
            targets.append(f"player '{tag}' ({n} field(s))")

        # Fluid network (matched by the special sentinel string)
        if tag == "DISTRIBUCION_INTERNA_FLUIDOS" and self.state["fluid_network"] is not None:
            n = self._apply_db_tag(self.state["fluid_network"], tag)
            targets.append(f"fluid network ({n} field(s))")

        if not targets:
            return (
                f"apply_safe_values: tag '{tag}' found in the database but "
                f"no matching element was found in the save file."
            )

        return "Applied safe values to: " + ", ".join(targets) + "."
