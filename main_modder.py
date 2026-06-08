'''
    Nucleares Mod Tool
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
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import xml.etree.ElementTree as ET
import os

from nucleares_io import read_save_file, write_save_file
from nucleares_state import SaveMemoryManager

class ModderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Nucleares Save Modder (Multi-Module)")
        self.root.geometry("800x680")
        
        self.memory = None
        self.manual_map = {}

        self.create_widgets()

    def log(self, message):
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')

    def create_widgets(self):
        # --- TOP: FILE I/O ---
        top_frame = tk.Frame(self.root, padx=10, pady=10)
        top_frame.pack(fill="x")
        
        tk.Button(top_frame, text="Load Save", command=self.load_file, bg="#4CAF50", fg="white", font=("Arial", 10, "bold"), padx=10).pack(side="left")
        self.btn_save = tk.Button(top_frame, text="Export File", command=self.save_file, bg="#f44336", fg="white", font=("Arial", 10, "bold"), padx=10, state="disabled")
        self.btn_save.pack(side="left", padx=10)
        self.lbl_file = tk.Label(top_frame, text="No file loaded.", fg="gray", font=("Arial", 10, "italic"))
        self.lbl_file.pack(side="left", padx=10)

        # --- NOTEBOOK TABS ---
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)

        self.tab_simple = tk.Frame(self.notebook, padx=20, pady=20)
        self.tab_advanced = tk.Frame(self.notebook, padx=20, pady=20)
        self.tab_reactor = tk.Frame(self.notebook, padx=20, pady=20)
        self.tab_manual = tk.Frame(self.notebook, padx=10, pady=10)

        self.notebook.add(self.tab_simple, text="Player Stats")
        self.notebook.add(self.tab_reactor, text="Reactor Control")
        self.notebook.add(self.tab_advanced, text="Maintenance")
        self.notebook.add(self.tab_manual, text="Manual XML Editor")

        self._build_simple_tab()
        self._build_reactor_tab()
        self._build_advanced_tab()
        self._build_manual_tab()

        # --- LOG AREA ---
        log_frame = tk.Frame(self.root, padx=10, pady=10)
        log_frame.pack(fill="x")
        tk.Label(log_frame, text="Console Output:", font=("Arial", 9, "bold")).pack(anchor="w")
        self.log_area = scrolledtext.ScrolledText(log_frame, height=6, state='disabled', bg="#f0f0f0")
        self.log_area.pack(fill="x")
        self.log("System Ready. Waiting for save file...")

    def _build_simple_tab(self):
        tk.Label(self.tab_simple, text="Modify Player Stats", font=("Arial", 14, "bold")).pack(anchor="w", pady=(0, 15))
        form_frame = tk.Frame(self.tab_simple)
        form_frame.pack(fill="x")

        tk.Label(form_frame, text="Money (Max 1B):").grid(row=0, column=0, sticky="e", pady=5)
        self.entry_money = tk.Entry(form_frame, width=20, font=("Arial", 12))
        self.entry_money.grid(row=0, column=1, pady=5, padx=10)

        tk.Label(form_frame, text="Level:").grid(row=1, column=0, sticky="e", pady=5)
        self.entry_level = tk.Entry(form_frame, width=20, font=("Arial", 12))
        self.entry_level.grid(row=1, column=1, pady=5, padx=10)

        tk.Label(form_frame, text="Experience:").grid(row=2, column=0, sticky="e", pady=5)
        self.entry_exp = tk.Entry(form_frame, width=20, font=("Arial", 12))
        self.entry_exp.grid(row=2, column=1, pady=5, padx=10)

        tk.Button(self.tab_simple, text="Apply Stats", command=self.do_simple_apply, bg="#2196F3", fg="white", font=("Arial", 11, "bold"), width=20).pack(pady=25)

    def _build_reactor_tab(self):
        tk.Label(self.tab_reactor, text="Physics & Simulation Override", font=("Arial", 14, "bold")).pack(anchor="w", pady=(0, 15))
        
        # Fixed: Passing the function name as a string so it safely evaluates later
        tk.Button(self.tab_reactor, text="🧪 Scrub Xenon & Iodine Poisoning (Avert Meltdown)", command=lambda: self.run_cheat("scrub_core_poisons"), width=50, height=2, bg="#673AB7", fg="white").pack(pady=5)
        tk.Button(self.tab_reactor, text="💨 Vent Pressures to Safe Levels (150 BAR)", command=lambda: self.run_cheat("normalize_pressures"), width=50, height=2, bg="#00BCD4", fg="black").pack(pady=5)
        tk.Button(self.tab_reactor, text="⚡ Maximize & Repair Backup Diesel Generators", command=lambda: self.run_cheat("max_backup_generators"), width=50, height=2, bg="#FFC107", fg="black").pack(pady=5)
        tk.Button(self.tab_reactor, text="💧 Refill All Coolant & Boron Reserves", command=lambda: self.run_cheat("flood_reserves"), width=50, height=2, bg="#03A9F4", fg="white").pack(pady=5)

    def _build_advanced_tab(self):
        tk.Label(self.tab_advanced, text="Component Maintenance", font=("Arial", 14, "bold")).pack(anchor="w", pady=(0, 15))
        # Fixed here as well
        tk.Button(self.tab_advanced, text="⚙️ Repair All Components", command=lambda: self.run_cheat("repair_all_objects"), width=30, height=2).pack(pady=10)

    def _build_manual_tab(self):
        header_frame = tk.Frame(self.tab_manual)
        header_frame.pack(fill="x", pady=(0, 5))
        
        tk.Label(header_frame, text="Select Node:").pack(side="left")
        self.combo_nodes = ttk.Combobox(header_frame, state="readonly", width=50)
        self.combo_nodes.pack(side="left", padx=10)
        self.combo_nodes.bind("<<ComboboxSelected>>", self.on_manual_node_select)

        tk.Button(header_frame, text="Apply Changes to Memory", command=self.do_manual_apply, bg="#FF9800", fg="white", font=("Arial", 9, "bold")).pack(side="right")
        self.text_manual = scrolledtext.ScrolledText(self.tab_manual, font=("Consolas", 10), wrap=tk.WORD)
        self.text_manual.pack(fill="both", expand=True)

    # --- EVENT HANDLERS ---
    def require_memory(self):
        if not self.memory:
            messagebox.showwarning("Warning", "Please load a save file first.")
            return False
        return True

    def load_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("Save Files", "*.txt *.xml"), ("All Files", "*.*")])
        if not filepath: return
        try:
            tree, root = read_save_file(filepath)
            self.memory = SaveMemoryManager(tree, root)
            self.lbl_file.config(text=os.path.basename(filepath), fg="blue")
            self.btn_save.config(state="normal")
            
            # Populate UI
            self.manual_map = self.memory.get_manual_map()
            self.combo_nodes['values'] = sorted(list(self.manual_map.keys()))
            self.text_manual.delete("1.0", tk.END)
            self.log(f"Successfully mapped {len(self.manual_map)} sub-systems into memory.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file:\n{e}")

    def save_file(self):
        if not self.require_memory(): return
        save_path = filedialog.asksaveasfilename(defaultextension=".xml", initialfile="savegame_MODDED.xml", filetypes=[("XML files", "*.xml")])
        if save_path:
            try:
                self.memory.commit_to_xml()
                write_save_file(self.memory.master_tree, save_path)
                self.log(f"Success! Modded file saved to: {save_path}")
                messagebox.showinfo("Success", "File exported successfully!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save file:\n{e}")

    def run_cheat(self, func_name):
        # Now it safely checks for memory FIRST
        if self.require_memory():
            # Looks up the function inside the memory manager by its string name
            func = getattr(self.memory, func_name)
            res = func()
            self.log(res)

    def do_simple_apply(self):
        if not self.require_memory(): return
        try:
            m = self.entry_money.get()
            e = self.entry_exp.get()
            l = self.entry_level.get()
            self.log(self.memory.set_simple_stats(m if m else 0, e if e else 0, l if l else 1))
        except Exception:
            messagebox.showerror("Error", "Please enter valid numbers.")

    def on_manual_node_select(self, event):
        selected = self.combo_nodes.get()
        if not selected or selected not in self.manual_map: return
        xml_element = self.manual_map[selected]
        xml_string = ET.tostring(xml_element, encoding="utf-8").decode("utf-8")
        self.text_manual.delete("1.0", tk.END)
        self.text_manual.insert("1.0", xml_string.strip())

    def do_manual_apply(self):
        if not self.require_memory(): return
        selected = self.combo_nodes.get()
        if not selected or selected not in self.manual_map: return

        new_xml = self.text_manual.get("1.0", tk.END).strip()
        try:
            new_element = ET.fromstring(new_xml)
            # In-place update of the Element to preserve references in the memory map
            target_element = self.manual_map[selected]
            target_element.clear()
            target_element.tag = new_element.tag
            target_element.attrib = new_element.attrib
            target_element.text = new_element.text
            target_element.tail = new_element.tail
            target_element.extend(list(new_element))
            
            self.log(f"Applied manual changes to memory for: {selected}")
        except ET.ParseError as e:
            messagebox.showerror("XML Error", f"Invalid XML syntax:\n\n{e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ModderApp(root)
    root.mainloop()
