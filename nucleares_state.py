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
import copy
from nucleares_io import decode_payload, encode_payload

class SaveMemoryManager:
    def __init__(self, master_tree, master_root):
        self.master_tree = master_tree
        self.master_root = master_root
        
        self.state = {
            "player": {},         
            "components": {},     
            "networks": [],       
            "objects": []         
        }
        
        self._parse_into_memory()

    def _parse_into_memory(self):
        """Scans the save file and safely builds the abstract map, checking for hidden payloads."""
        
        # 1. Map Player Data
        for tag in ["JUGADOR", "LOGROS_MLIBRE"]:
            node = self.master_root.find(f".//{tag}")
            if node is not None and node.text:
                self.state["player"][tag] = decode_payload(node.text)

        # 2. Map Reactor Components (Dynamic Check for Inception Payloads)
        comps_node = self.master_root.find(".//componentes")
        if comps_node is not None:
            for comp in comps_node:
                if comp.text and "<?xml" in comp.text:
                    self.state["components"][comp.tag] = {
                        "type": "encoded",
                        "master_element": comp,
                        "inner_xml": decode_payload(comp.text)
                    }
                else:
                    self.state["components"][comp.tag] = {
                        "type": "direct",
                        "master_element": comp,
                        "inner_xml": comp
                    }

        # 3. Map Fluid Networks (Dynamic Check)
        for net in self.master_root.findall(".//NODE_NETWORKS"):
            if net.text and "<?xml" in net.text:
                self.state["networks"].append({
                    "type": "encoded",
                    "master_element": net,
                    "inner_xml": decode_payload(net.text)
                })
            else:
                self.state["networks"].append({
                    "type": "direct",
                    "master_element": net,
                    "inner_xml": net
                })

        # 4. Map Objects (Pipe-separated Strings)
        obj_node = self.master_root.find(".//objetos")
        if obj_node is not None:
            for obj in obj_node:
                if obj.text and "|" in obj.text:
                    parts = obj.text.split("|")
                    if len(parts) >= 4:
                        try:
                            inner_xml = decode_payload(parts[-1])
                            self.state["objects"].append({
                                "master_element": obj,
                                "parts_prefix": parts[:-1],
                                "inner_xml": inner_xml
                            })
                        except ValueError:
                            continue

    def commit_to_xml(self):
        """Maps the abstracted in-memory state back onto the master XML tree."""
        for tag, inner_element in self.state["player"].items():
            node = self.master_root.find(f".//{tag}")
            if node is not None:
                node.text = encode_payload(inner_element)
                
        for tag, comp_data in self.state["components"].items():
            if comp_data["type"] == "encoded":
                comp_data["master_element"].text = encode_payload(comp_data["inner_xml"])
                
        for net_data in self.state["networks"]:
            if net_data["type"] == "encoded":
                net_data["master_element"].text = encode_payload(net_data["inner_xml"])

        for obj_data in self.state["objects"]:
            encoded_xml = encode_payload(obj_data["inner_xml"])
            full_string = "|".join(obj_data["parts_prefix"] + [encoded_xml])
            obj_data["master_element"].text = full_string

    # ==========================================
    # UTILITIES & GETTERS
    # ==========================================
    def get_manual_map(self):
        mapping = {}
        for tag, elem in self.state["player"].items():
            mapping[f"[Player] {tag}"] = elem
        for tag, comp_data in self.state["components"].items():
            mapping[f"[Component] {tag}"] = comp_data["inner_xml"]
        for i, net_data in enumerate(self.state["networks"]):
            net = net_data["inner_xml"]
            name_node = net.find(".//Name")
            n = name_node.text if name_node is not None else f"Network_Index_{i}"
            mapping[f"[Fluid] {n}"] = net
        for obj in self.state["objects"]:
            obj_id = obj["parts_prefix"][0]
            mapping[f"[Object] {obj_id}"] = obj["inner_xml"]
        return mapping

    def _set_dict_value(self, root_element, dict_name, key_name, new_value, value_tag):
        dict_element = root_element.find(f".//{dict_name}")
        if dict_element is None: return False
        children = list(dict_element)
        for i in range(len(children) - 1):
            if children[i].tag == "string" and children[i].text == key_name:
                if children[i+1].tag == value_tag:
                    children[i+1].text = str(new_value)
                    return True
        return False

    # ==========================================
    # MODIFICATION LOGIC (CHEATS)
    # ==========================================
    def set_simple_stats(self, money, exp, level):
        # Apply strict caps to safe thresholds to keep user interface clear and prevent serialization faults
        money = min(max(float(money if money else 0), 0.0), 1000000000.0)
        exp = min(max(float(exp if exp else 0), 0.0), 1000000000.0)
        level = min(max(int(level if level else 1), 1), 100)
        
        logros = self.state["player"].get("LOGROS_MLIBRE")
        if logros is not None:
            for tag, val in [("Puntos", money), ("NuevoPuntos", money), ("Experiencia", exp), ("Nivel", level)]:
                elem = logros.find(f".//{tag}")
                if elem is not None:
                    elem.text = str(val)
                else:
                    elem = ET.SubElement(logros, tag)
                    elem.text = str(val)
                    
        jugador = self.state["player"].get("JUGADOR")
        if jugador is not None:
            self._set_dict_value(jugador, "_valoresFloat", "dinero", money, "float")
            self._set_dict_value(jugador, "_valoresFloat", "experiencia", exp, "float")
            self._set_dict_value(jugador, "_valoresFloat", "nivel", float(level), "float")
            
        return f"Stats Applied safely: Money={money:,.0f}, Level={level}, EXP={exp:,.0f}"

    def repair_all_objects(self):
        count = 0
        
        # 1. Repair Pipeline Objects
        for obj in self.state["objects"]:
            inner = obj["inner_xml"]
            repaired = False
            
            if self._set_dict_value(inner, "_valoresFloat", "porcentaje_roto", 0.0, "float"): repaired = True
            if self._set_dict_value(inner, "_valoresFloat", "desgaste", 0.0, "float"): repaired = True
            if self._set_dict_value(inner, "_valoresFloat", "temperatura", 20.0, "float"): repaired = True
            
            for tag in ["Integridad"]:
                elem = inner.find(f".//{tag}")
                if elem is not None and elem.text != "100.0" and elem.text != "100":
                    elem.text = "100.0"
                    repaired = True
            if repaired: count += 1

        # 2. Repair Major Systems
        for comp_name, comp_data in self.state["components"].items():
            inner = comp_data["inner_xml"]
            repaired = False
            for tag in ["Integridad", "IntegridadCalentadores", "IntegridadReliefTank"]:
                elem = inner.find(f".//{tag}")
                if elem is not None and elem.text != "100.0" and elem.text != "100":
                    elem.text = "100.0"
                    repaired = True
            for tag in ["RequiereMantenimiento", "IsContaminado", "DesactivadoPorFaltaDeSuministro"]:
                elem = inner.find(f".//{tag}")
                if elem is not None and elem.text == "true":
                    elem.text = "false"
                    repaired = True
            if repaired: count += 1
            
        return f"Fully repaired {count} reactor objects and systems."

    def scrub_core_poisons(self):
        core_data = self.state["components"].get("NUCLEO")
        if not core_data: return "Failed: Could not find <NUCLEO> in components."
        core = core_data["inner_xml"]
        
        modified = []
        
        # FLOAT VALUES (Require decimals)
        for tag in ["XenonConcentracion", "YodoConcentracion", "ReactividadXenon", "ReactividadYodo"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = "0.0"
                modified.append(tag)
                
        # INTEGER VALUES (Crash C# if given decimals)
        for tag in ["_minutosAcumuladosEnvenenamientoXenon", "ContadorMasaCritica"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = "0"
                modified.append(tag)
            
        # BOOLEAN VALUES (String false)
        for tag in ["ExplosionInminente", "Flag_PerdioMasaCritica", "AlertaPorSituacionInsegura"]:
            elem = core.find(f".//{tag}")
            if elem is not None:
                elem.text = "false"
                modified.append(tag)
        
        return f"Core Scrubbed Safely. Modified attributes: {', '.join(modified) if modified else 'None'}"

    def normalize_pressures(self):
        pres_data = self.state["components"].get("PRESURIZADOR")
        evap_data = self.state["components"].get("EVAPORADOR")
        
        if pres_data:
            pres = pres_data["inner_xml"]
            elem = pres.find(".//PresionFisicaBAR")
            if elem is not None: elem.text = "150.0"
            elem = pres.find(".//TemperaturaFisicaMaxima")
            if elem is not None: elem.text = "320.0"
            
        if evap_data:
            evap = evap_data["inner_xml"]
            elem = evap.find(".//PresionFisicaBAR")
            if elem is not None: elem.text = "70.0"
            
        return "Pressures Normalized: Pressurizer safely vented to 150 BAR."

    def max_backup_generators(self):
        sum_data = self.state["components"].get("SUMINISTROINTERNO")
        if not sum_data: return "Failed: Could not find <SUMINISTROINTERNO>."
        suministro = sum_data["inner_xml"]
        
        electros = suministro.findall(".//CElectrogeno")
        for e in electros:
            c = e.find(".//Combustible")
            if c is not None: c.text = "99999.0"
            i = e.find(".//IsContaminado")
            if i is not None: i.text = "false"
            m = e.find(".//RequiereMantenimiento")
            if m is not None: m.text = "false"
            
        return f"Generators Secured: Refueled {len(electros)} backup diesel generators."

    def flood_reserves(self):
        count = 0
        for net_data in self.state["networks"]:
            net = net_data["inner_xml"]
            liquidos = net.findall(".//Liquido")
            for liq in liquidos:
                tipo = liq.find("Tipo")
                if tipo is not None and tipo.text and tipo.text.strip() in ["AGUA", "BORO"]:
                    cant = liq.find("Cantidad")
                    if cant is not None: 
                        cant.text = "500000.0"
                        count += 1
        return f"Fluids Flooded: Maxed out {count} Water and Boron fluid nodes."
