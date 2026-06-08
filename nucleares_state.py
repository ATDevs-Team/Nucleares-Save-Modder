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
import copy
from nucleares_io import decode_payload, encode_payload

def _html_decode(text):
    """HTML-unescape a component payload, then parse as XML."""
    clean = html.unescape(text.strip())
    if 'encoding="utf-16"' in clean:
        clean = clean.replace('encoding="utf-16"', 'encoding="utf-8"')
    return ET.fromstring(clean)


class SaveMemoryManager:
    def __init__(self, master_tree, master_root):
        self.master_tree = master_tree
        self.master_root = master_root

        self.state = {
            "player": {},
            "components": {},
            "fluid_network": None,   # Single decoded DIF element (mutable in-place)
            "fluid_network_node": None,  # The raw master XML node for write-back
            "objects": []
        }

        self._parse_into_memory()

    # ------------------------------------------------------------------
    # PARSING
    # ------------------------------------------------------------------

    def _parse_into_memory(self):
        """Builds the abstract in-memory state from the master XML tree."""

        # 1. Player nodes (HTML-encoded XML blobs)
        for tag in ["JUGADOR", "LOGROS_MLIBRE"]:
            node = self.master_root.find(f".//{tag}")
            if node is not None and node.text:
                self.state["player"][tag] = _html_decode(node.text)

        # 2. Reactor components (also HTML-encoded XML blobs inside <componentes>)
        comps_node = self.master_root.find(".//componentes")
        if comps_node is not None:
            for comp in comps_node:
                if comp.text and ("<?xml" in comp.text or "&lt;" in comp.text):
                    # HTML-encoded XML payload
                    self.state["components"][comp.tag] = {
                        "type": "encoded",
                        "master_element": comp,
                        "inner_xml": _html_decode(comp.text),
                    }
                else:
                    self.state["components"][comp.tag] = {
                        "type": "direct",
                        "master_element": comp,
                        "inner_xml": comp,
                    }

        # 3. Fluid network — lives in <DISTRIBUCION_INTERNA_FLUIDOS>, NOT <NODE_NETWORKS>
        dif_node = self.master_root.find(".//DISTRIBUCION_INTERNA_FLUIDOS")
        if dif_node is not None and dif_node.text:
            self.state["fluid_network_node"] = dif_node
            self.state["fluid_network"] = _html_decode(dif_node.text)

        # 4. Objects — pipe-separated strings; only those whose LAST segment is an
        #    XML payload are mapped.  Non-XML objects are silently skipped (they
        #    carry no repairable state).
        obj_node = self.master_root.find(".//objetos")
        if obj_node is not None:
            for obj in obj_node:
                if not (obj.text and "|" in obj.text):
                    continue
                parts = obj.text.split("|")
                # The XML payload, when present, is always the last segment
                last = parts[-1]
                if "<?xml" not in last and "&lt;" not in last:
                    continue
                try:
                    inner_xml = _html_decode(last)
                    self.state["objects"].append({
                        "master_element": obj,
                        "parts_prefix": parts[:-1],
                        "inner_xml": inner_xml,
                    })
                except (ValueError, ET.ParseError):
                    continue

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

    # ------------------------------------------------------------------
    # UTILITIES
    # ------------------------------------------------------------------

    def get_manual_map(self):
        mapping = {}
        for tag, elem in self.state["player"].items():
            mapping[f"[Player] {tag}"] = elem
        for tag, comp_data in self.state["components"].items():
            mapping[f"[Component] {tag}"] = comp_data["inner_xml"]
        # Fluid network exposed as a single entry
        dif = self.state["fluid_network"]
        if dif is not None:
            mapping["[Fluid] DISTRIBUCION_INTERNA_FLUIDOS"] = dif
        for obj in self.state["objects"]:
            obj_id = obj["parts_prefix"][0] if obj["parts_prefix"] else "unknown"
            mapping[f"[Object] {obj_id}"] = obj["inner_xml"]
        return mapping

    def _set_dict_value(self, root_element, dict_name, key_name, new_value, value_tag):
        dict_element = root_element.find(f".//{dict_name}")
        if dict_element is None:
            return False
        children = list(dict_element)
        for i in range(len(children) - 1):
            if children[i].tag == "string" and children[i].text == key_name:
                if children[i + 1].tag == value_tag:
                    children[i + 1].text = str(new_value)
                    return True
        return False

    # ------------------------------------------------------------------
    # MODIFICATION LOGIC (CHEATS)
    # ------------------------------------------------------------------

    def set_simple_stats(self, money, exp, level):
        money = min(max(float(money if money else 0), 0.0), 1_000_000_000.0)
        exp   = min(max(float(exp   if exp   else 0), 0.0), 1_000_000_000.0)
        level = min(max(int  (level if level else 1), 1), 100)

        logros = self.state["player"].get("LOGROS_MLIBRE")
        if logros is not None:
            for tag, val in [("Puntos", money), ("NuevoPuntos", money),
                             ("Experiencia", exp), ("Nivel", level)]:
                elem = logros.find(f".//{tag}")
                if elem is not None:
                    elem.text = str(val)
                else:
                    ET.SubElement(logros, tag).text = str(val)

        jugador = self.state["player"].get("JUGADOR")
        if jugador is not None:
            self._set_dict_value(jugador, "_valoresFloat", "dinero",     money,         "float")
            self._set_dict_value(jugador, "_valoresFloat", "experiencia", exp,           "float")
            self._set_dict_value(jugador, "_valoresFloat", "nivel",       float(level),  "float")

        return f"Stats Applied: Money={money:,.0f}, Level={level}, EXP={exp:,.0f}"

    def repair_all_objects(self):
        count = 0

        # 1. Objects with XML payloads (fuel rods, pumps, valves, etc.)
        for obj in self.state["objects"]:
            inner = obj["inner_xml"]
            repaired = False
            if self._set_dict_value(inner, "_valoresFloat", "porcentaje_roto", 0.0, "float"):
                repaired = True
            if self._set_dict_value(inner, "_valoresFloat", "desgaste", 0.0, "float"):
                repaired = True
            if self._set_dict_value(inner, "_valoresFloat", "temperatura", 20.0, "float"):
                repaired = True
            for tag in ["Integridad"]:
                elem = inner.find(f".//{tag}")
                if elem is not None and elem.text not in ("100.0", "100"):
                    elem.text = "100.0"
                    repaired = True
            if repaired:
                count += 1

        # 2. Major reactor sub-systems (components)
        for comp_name, comp_data in self.state["components"].items():
            inner = comp_data["inner_xml"]
            repaired = False
            for tag in ["Integridad", "IntegridadCalentadores", "IntegridadReliefTank"]:
                for elem in inner.findall(f".//{tag}"):
                    if elem.text not in ("100.0", "100"):
                        elem.text = "100.0"
                        repaired = True
            for tag in ["RequiereMantenimiento", "IsContaminado",
                        "DesactivadoPorFaltaDeSuministro", "RequiereMantenimientoCalentadores"]:
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
                if elem.text not in ("100.0", "100"):
                    elem.text = "100.0"
                    count += 1

        return f"Fully repaired {count} reactor objects and systems."

    def scrub_core_poisons(self):
        core_data = self.state["components"].get("NUCLEO")
        if not core_data:
            return "Failed: Could not find <NUCLEO> in components."
        core = core_data["inner_xml"]
        modified = []

        for tag in ["XenonConcentracion", "YodoConcentracion",
                    "ReactividadXenon",   "ReactividadYodo"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = "0.0"
                modified.append(tag)

        for tag in ["_minutosAcumuladosEnvenenamientoXenon", "ContadorMasaCritica"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = "0"
                modified.append(tag)

        for tag in ["ExplosionInminente", "Flag_PerdioMasaCritica",
                    "AlertaPorSituacionInsegura"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = "false"
                modified.append(tag)

        return (f"Core Scrubbed. Modified: "
                f"{', '.join(modified) if modified else 'None'}")

    def normalize_pressures(self):
        """
        Safely vent pressures.

        PRESURIZADOR:
          PresionFisicaBAR      → set to 160 (matches PresionOperativaBAR setpoint)
          PresionMaxBAR         → left alone (hardware limit, not a runtime value)
          TemperaturaFisicaMaxima → left alone (it's a hardware limit, not current temp)

        EVAPORADOR:
          PresionFisicaBAR      → set to 60 (safe secondary-side operating pressure)
          TemperaturaFisicaMaxima → left alone (hardware limit)
        """
        pres_data = self.state["components"].get("PRESURIZADOR")
        evap_data = self.state["components"].get("EVAPORADOR")
        msgs = []

        if pres_data:
            pres = pres_data["inner_xml"]
            # Only touch the CURRENT physical pressure, never the max/limit fields
            elem = pres.find(".//PresionFisicaBAR")
            if elem is not None:
                elem.text = "160.0"   # match PresionOperativaBAR so no sudden delta
                msgs.append("Pressurizer → 160 BAR")

        if evap_data:
            evap = evap_data["inner_xml"]
            elem = evap.find(".//PresionFisicaBAR")
            if elem is not None:
                elem.text = "60.0"
                msgs.append("Steam generator → 60 BAR")

        if not msgs:
            return "normalize_pressures: no pressure elements found."
        return "Pressures normalized: " + ", ".join(msgs) + "."

    def max_backup_generators(self):
        sum_data = self.state["components"].get("SUMINISTROINTERNO")
        if not sum_data:
            return "Failed: Could not find <SUMINISTROINTERNO>."
        suministro = sum_data["inner_xml"]

        # CElectrogeno elements are nested anywhere under SUMINISTROINTERNO
        electros = suministro.findall(".//CElectrogeno")
        for e in electros:
            c = e.find(".//Combustible")
            if c is not None:
                c.text = "99999.0"
            for tag in ["IsContaminado", "RequiereMantenimiento"]:
                t = e.find(f".//{tag}")
                if t is not None:
                    t.text = "false"
            # Repair integrity
            i = e.find(".//Integridad")
            if i is not None:
                i.text = "100.0"

        return (f"Generators Secured: Refueled {len(electros)} "
                f"backup diesel generator(s).")

    def flood_reserves(self):
        """
        Fill every AGUA and BORO Cantidad node in the fluid network.

        The fluid network is a single encoded blob at DISTRIBUCION_INTERNA_FLUIDOS.
        Fluid is stored in SSave (Tubos) and SSaveContenedores (Contenedores).
        Each has a Contenido/_liquidos list of <Liquido><Tipo>…<Cantidad>… nodes.
        """
        dif = self.state["fluid_network"]
        if dif is None:
            return "Failed: Fluid network (DISTRIBUCION_INTERNA_FLUIDOS) not found."

        agua_count = 0
        boro_count = 0

        for liquido in dif.findall(".//Liquido"):
            tipo = liquido.find("Tipo")
            cant = liquido.find("Cantidad")
            if tipo is None or cant is None:
                continue
            tipo_text = tipo.text.strip() if tipo.text else ""
            if tipo_text == "AGUA":
                cant.text = "500000.0"
                agua_count += 1
            elif tipo_text == "BORO":
                cant.text = "500000.0"
                boro_count += 1

        return (f"Fluids Flooded: filled {agua_count} water node(s) "
                f"and {boro_count} boron node(s).")
