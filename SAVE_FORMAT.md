# Nucleares Save File Format — Reverse-Engineering Notes

This document records everything discovered about the structure of
`savegame_025_*.xml` files (save format `<version>25</version>`, i.e.
Nucleares **V2.2.25.x**) by inspecting 7 real save files from a live
playthrough (two separate games, save slots 61–66 + AUTOSAVE). It is meant
as a reference for extending `nucleares_state.py` and for redesigning the
`.nsmdb` database format.

---
## 1. Outer file shape

```xml
<?xml version="1.0" encoding="utf-8"?>
<nucleares>
  <version>25</version>
  <componentes> ... </componentes>
  <objetos> ... </objetos>
  <DISTRIBUCION> ... </DISTRIBUCION>
  <GESTION_INTERNA_ENERGIA> ... </GESTION_INTERNA_ENERGIA>
  <JUGADOR> ... </JUGADOR>
  <AMBIENTE> ... </AMBIENTE>
  <HISTORIA> ... </HISTORIA>
  <ESCENARIO_LOCURA_AO> ... </ESCENARIO_LOCURA_AO>
  <EVENTOS> ... </EVENTOS>
  <DISTRIBUCION_INTERNA_FLUIDOS> ... </DISTRIBUCION_INTERNA_FLUIDOS>
  <LOGROS_MLIBRE> ... </LOGROS_MLIBRE>
  <LOGISTICA> ... </LOGISTICA>
  <INTERFAZ> ... </INTERFAZ>
  <MANTENIMIENTO> ... </MANTENIMIENTO>
  <AUDITORIAS> ... </AUDITORIAS>
  <CONFIG_ALARMAS> ... </CONFIG_ALARMAS>
  <TRANSFORMACION> ... </TRANSFORMACION>
  <COMUNICACIONES> ... </COMUNICACIONES>
  <QUIMICOS_CMOTOR> ... </QUIMICOS_CMOTOR>
  <CORTES_PROGRAMADOS> ... </CORTES_PROGRAMADOS>
</nucleares>
```

Almost every one of these top-level nodes (everything except `version`,
`componentes`, and `objetos`) is a **text node**, not real child elements:
its `.text` is an HTML-escaped, UTF-16-declared inner XML document (a
"payload"). This is exactly what `nucleares_io.decode_payload` /
`encode_payload` and `nucleares_state._html_decode` already handle:

```
&lt;?xml version="1.0" encoding="utf-16"?&gt;
&lt;CSaveClass xmlns:xsd="..." xmlns:xsi="..."&gt;
  &lt;SomeField&gt;value&lt;/SomeField&gt;
  ...
&lt;/CSaveClass&gt;
```

The wrapper class name varies (`CSaveClass`, `CSave`, `CSaveAlarmas`, ...)
but it's irrelevant — only the children matter. One outlier: `DISTRIBUCION`
was observed empty (`<CSaveClass></CSaveClass>` — a placeholder never
populated in these saves; `DISTRIBUCION_INTERNA_FLUIDOS` is the real fluid
network, see §5).

**Quirk observed on save `00066` only**: no `xmlns:xsd`/`xmlns:xsi`
attributes, single-quoted XML declaration (`<?xml version='1.0' ...?>`) and
no UTF-8 BOM at the start of the file, vs. double-quoted + xsi/xsd + BOM on
the other 6 saves. Both parse fine with `ET.fromstring`/`html.unescape`
today, so this is cosmetic (likely just a different Nucleares
build/language runtime serializer), but worth keeping in mind if a save
ever fails to parse — don't assume the header is fixed.

### Value encoding rules (apply everywhere in the payloads)
- Booleans: lowercase `true` / `false` text.
- Floats: plain decimal, or scientific notation for very large numbers
  (e.g. `1.2252137E+09`, `8.638818E-14`). Any code that rewrites a float
  field should use `str(float(...))`, not blindly copy formatting, since
  Python's `str(float)` never emits `E+09`-style notation for the ranges
  NSM cares about (money/exp) — that's fine, the game reads plain decimals
  too.
- Empty string fields serialize as `<Tag></Tag>` (ElementTree renders this
  as `.text = None` or `""` depending on how it's built — both read back
  fine).
- 3D vectors are `<X><x/><y/><z/></X>`; quaternion rotations add `<w/>` and
  a nested `<eulerAngles><x/><y/><z/></eulerAngles>`.
- Repeated-field ring buffers (rolling history arrays for graphs, e.g.
  `_promediarTemp`, `_vectorVariaciones`, `_promedioXenonEnCiclo`) are
  `<Name><float>..</float><float>..</float>...</Name>` with a fixed count
  (commonly 3, 10, 20, or 24 `<float>` entries) plus sibling scalar fields
  like `_puntero`, `_habilitado`, `_ultimoValor`. These are cosmetic/UI
  history and safe to ignore for cheats.

### ⚠️ Important: the `_valoresFloat` / `_valoresInt` / `_valoresBool` /
### `_valoresString` dict-container fallback in `nucleares_state.py` is dead code

`SaveMemoryManager._set_dict_value` and `_apply_db_tag` fall back to
searching for `_valoresFloat`/`_valoresInt`/`_valoresBool`/`_valoresString`
dictionaries (a list of alternating `<string>key</string><TYPE>value</TYPE>`
pairs) when a direct `find(".//KEY")` misses. **None of these four
container tags appear anywhere in any of the 7 real save files inspected**
(verified with a raw substring search across all of them — the only
`_valores*`-prefixed tag that exists is the unrelated `_valores` /
`_valorMin` / `_valorMax` history-graph triplet used by several components'
UI trend buffers, e.g. inside `SERVICIOSEA`/turbine graphs). This means:

- `set_simple_stats`'s two `_set_dict_value(jugador, "_valoresFloat", ...)`
  calls for `dinero`/`experiencia`/`nivel` **never actually write anything**
  — `JUGADOR` has no such dict in the current format. The function only
  works because it *also* writes `Puntos`/`Experiencia`/`Nivel` directly
  into `LOGROS_MLIBRE`, which does use plain named elements. This is
  presumably what `tested_versions.md` means by "NSM itself is why it
  doesn't fully work" for V2.2.25.217.
- `_apply_db_tag`'s "attempt 2: dict-value serialisation" branch is
  currently unreachable for every tag observed in real saves.

This dict-container format was likely how an **older** Nucleares save
version stored typed values (hence the code being written defensively for
it), but the current V25 format stores every field as a directly-named XML
element instead (`<dinero>` would just be `<Puntos>`/`<Experiencia>` etc. —
except money/exp/level don't even live under those names in `JUGADOR`,
they live in `LOGROS_MLIBRE`, see §4). **When reworking NSMDB, the
dict-container fallback should either be removed, or explicitly
special-cased as "legacy/未confirmed" rather than the primary lookup path.**
Direct `find(f".//{key}")` is the only mechanism that has been confirmed to
work against real V25 saves.

---
## 2. `<componentes>` — the 18 reactor/building subsystems

Each child of `<componentes>` is one component, keyed by its XML tag name
(`comp.tag`), with an HTML-escaped inner-XML payload exactly like the
top-level nodes. Observed in every save (schema is stable across all 7
files and both playthroughs):

| Tag | Wrapper | Purpose | Notable fields |
|---|---|---|---|
| `MOTOR` | `CSaveClass` | Master plant "engine" — overall run state | `Activado`, `ModoOperativo` (`NOMINAL`/`SHUTDOWN`), `HuboExplosion`, `HayParadaDeEmergencia` |
| `REFRIGERANTE` | `CSaveClass` | Coolant system | `Base` (see below), `Volumen`, `RequiereMantenimiento` |
| `CONTROL` | `CSaveClass` | Control-rod drive mechanism | `PosDestino`, `PotenciaMotor`, `_temperatura` |
| `COMBUSTION` | `CSaveClass` | Diesel/fuel combustion subsystem | `Estado` (`CARGADO`/`DESCARGADO`), `Cantidad` (fuel amount), `Temperatura` |
| `NUCLEO` | `CSaveClass` | **The reactor core** — see detail below | many |
| `GENERADOR` | `CSaveClass` | Turbine generator | `Conectado`, `_vectorTurbinas` (3-slot RPM/output array), `TurbinaSeleccionadaParaSync` |
| `BLINDAJE` | `CSaveClass` | Radiation shielding | `Integridad`, `Temperatura`, `RadiacionDelNucleo` |
| `SERVICIOSEA` | `CSaveClass` | Cooling towers / essential service water | `Esws` (tower specs), `Estado` (`DETENIDO`/running), valve positions |
| `SUMINISTROINTERNO` | `CSaveClass` | Internal power supply — batteries + diesel generators | `RacksBaterias` (9× `CRackBaterias`), `Electrogenos/Registrados` (list of `CElectrogeno`, see §2.1), power flow fields |
| `PRESURIZADOR` | `CSaveClass` | Pressurizer | `Presurizador` (specs incl. `PresionFisicaBAR`), `IntegridadReliefTank`, heater fields |
| `EVAPORADOR` | `CSaveClass` | Steam generator | `Evaporador` (`PresionFisicaBAR`, `TemperaturaFisicaMaxima`) |
| `CONDENSADOR` | `CSaveClass` | Condenser | `Condensador` (mirrors Evaporador's shape) |
| `ESTADOSFISICOS` | `CSaveClass` | Cached boolean physics-state flags (derived/read-only) | `_nucleoReactivo`, `_combustibleActivo`, `_generandoEnergia`, ... |
| `EVENTOSINTERNOS` | `CSaveClass` | Internal event/edge-trigger flags | `Flag_AlcanzoMasaCritica`, `Flag_PerdioMasaCritica`, `AlertaPorSituacionInsegura` |
| `ASISTENTE` | `CSaveClass` | The AO robot assistant (NPC) — position, current task/command, automation toggles | `Posicion`/`Rotacion`, `DestinoObjetivo`, `ComandoActivo`, `AutomatizarPresurizador` etc. |
| `ASENSOR_PRINCIPAL` | `CSaveClass` | Main elevator/lift | `EjeY_ActualCoche` (just its Y position) |
| `QUIMICOS` | `CSave` | Chemical delivery truck/service | `Servicio` (e.g. `RECARGA_FUEL`), arrival time, `IsCamionConectado` |
| `CONTROL_NEW_CORE` | `CSaveClass` | New-core-model control logic — control rod banks & fuel bays | `_bancos` (9× `CBancoSaveClass`: `_cantidadBarras`, `IsActivo`), `_bahias` (9× `CBahiaSaveClass`: per-bay fuel state, see §2.2) |

Every component with a physical presence embeds a common **`Base`** block
(10 fields) used consistently for power/activation/integrity bookkeeping:

```
<Base>
  <_encendido>true|false</_encendido>            powered on
  <_energia>true|false</_energia>                 has power available
  <Activo>true|false</Activo>                     actively running
  <Integridad>0-100</Integridad>                   health %
  <DesactivadoPorFaltaDeSuministro>bool</...>       forced off (no power)
  <AmbienteAlarmaCritica>bool</...>
  <AmbienteAlarmaAlta>bool</...>
  <UltimaPerdidaDeIntegridad_Dias/Hora/Minuto>       timestamp of last damage
</Base>
```
This is the natural generic target for a future "repair all" / "power
cycle all" cheat — any component's `Base/Integridad` can be set the same
way `repair_all_objects` already does for the top-level `Integridad` tags.

### 2.1 NUCLEO (reactor core) — full field list
```
Integridad, Estado (NOREACTIVO|REACTIVO), Tipo (SIMPLE),
TemperaturaOptima, TemperaturaMaxima, PresionMaxima,
PosValvulaEscape, PosValvulaEscapeEstablecida,
ContadorMasaCritica, ExplosionInminente,
FactorAbsorcion, CalorGenerado, WearHorasDeUso,
_lastGeneracionCalor, _lastPerdidaCalor,
_primerFision_dia/_hora/_minuto,
XenonConcentracion, YodoConcentracion,
_lastPicoCalorGenerado_dia/_hora/_minuto,
_lastCaidaCalorGenerado_dia/_hora/_minuto,
_minutosAcumuladosEnvenenamientoXenon,
_lastMinutoAcumuladoEnvenenamientoXenon,
Yodo_PicoPotencia, Base (see above),
ReactividadXenon, ReactividadYodo,
_cantidadYodoEnCiclo, _punteroXenonEnCiclo, _punteroYodoEnCiclo,
_masaCriticaContada, _horasDeUso,
_promedioXenonEnCiclo (10 floats, history), _promedioYodoEnCiclo (10 floats),
Moleculas (usually empty), FactorEstadoCriticidad, _radioactividadPasiva
```
`scrub_core_poisons` already targets the right fields
(`XenonConcentracion`, `YodoConcentracion`, `ReactividadXenon`,
`ReactividadYodo`, the two `_minutosAcumulados.../Contador...` counters,
and the three boolean alert flags) — confirmed correct against real data.
Save `00065` (reactor tripped) had `XenonConcentracion=580.0065` while
`NOREACTIVO`, which is a good regression-test fixture for that cheat.

### 2.2 SUMINISTROINTERNO → `CElectrogeno` (backup diesel generators)
```
Id, Estado (INACTIVO|...), Tipo (CLASE_B|...), CapacidadkW,
MinutosDemoraEncendido, FactorConsumoCombustible, Combustible,
IsContaminado, Activo, PotenciaGenerada_kW, Integridad, Modo (MANUAL|AUTOMATICO),
RequiereMantenimiento
```
Matches `max_backup_generators`'s target fields exactly (`Combustible`,
`Integridad`, `IsContaminado`, `RequiereMantenimiento`).

### 2.3 CONTROL_NEW_CORE → `_bahias` → `CBahiaSaveClass` (fuel bays)
```
Temperatura, IsVacia, Estado (INTERIOR|...), Accion, IsCompuertaAbierta,
IsSeguroAbrirCompuerta, IsAbiertaInsegura, IsBahiaAptaLoad, IsBahiaAptaUnload,
IdCombustible (links to a BloqueCombustible object's Id — see §3), PosicionPiston, IsDestruida
```
9 bays observed (`_bancos` also has 9 entries, one control-rod bank per
bay). `_cantidadBarras` (control rods per bank, observed `8`) plus
`IsActivo`/`_scramSolicitado`/`_directo` per bank is where a future "SCRAM"
or "insert/withdraw all control rods" cheat would live.

---
## 3. `<objetos>` — the object/entity list

Each `<objetos>` child is a generically-named element (tag = sanitized
object name) whose `.text` is a **pipe (`|`) delimited** record. There are
three observed shapes:

**A. Scene/decoration objects (18 fields, no game-logic payload)** — pure
Unity transform data for static props (carts, beams, hooks, rotor meshes,
...):
```
0: internal id            e.g. "Cart (1)"
1: display name            e.g. "Cart (1)" (often == field 0)
2: object class            "GameObject"
3: "True"/"False"          active?
4: position   "(x, y, z)"
5: rotation   "(x, y, z)"  (Euler degrees)
6: scale      "(x, y, z)"
7-9: extra vectors (velocity/anchor?) "(x, y, z)"
10: "False"/"True"
11: parent/group name       e.g. "Andamios", "GE_Generador01"
12: "False"/"True"
13-15: usually empty strings
16: "TRUE"/"FALSE"
17 (last): "TRUE"/"FALSE"
```
These 18-field records are pure Unity-side bookkeeping (positions of decor
meshes); NSM has no reason to ever touch them.

**B. Game-logic objects with embedded payload (≥4 fields, last field is an
HTML-escaped XML payload)** — e.g. `BloqueCombustible<N>` (fuel rod
assemblies):
```
0: internal id       "BloqueCombustible9"
1: display name       (same)
2: object class        "BloqueCombustible"
3 (last): HTML-escaped <CSaveClass> payload (decode with the same
   _html_decode helper as components) containing:
     Clase (CLASE_B|CLASE_C), Integridad, Temperatura, Cantidad,
     Ubicacion (NUCLEO|CONTENEDOR), Id, Inutilizable, SelloRoto,
     _lastConsumo, _lastIntegridad, _factorPotencia,
     _idBahia (links back to CONTROL_NEW_CORE/_bahias, -1 if not loaded),
     _nombreContenedor, _pos/_rot/_sca (3D vectors),
     _minutosHastaMaximizar(Last), _idBahiaPorSensor, _idContenedorPorSensor,
     _afectadoPorGravedad, IsAgarradoPorGrua, Nombre
```
This is already exactly what `SaveMemoryManager._parse_into_memory`'s
objects-loop extracts (`parts_prefix` = fields 0..2, `inner_xml` = decoded
field 3). Confirmed correct. `Ubicacion=NUCLEO` fuel blocks were the ones
with `SelloRoto=true` and `Integridad=89` in save 00064 (worn/leaking seal
— a good target for a future "reseal/refuel all fuel rods" cheat: set
`SelloRoto=false`, bump `Integridad`, refill `Cantidad`).

**C. Switches/simple toggles (4 fields, last field is a bare literal, not
XML)** — e.g. `InterruptorTecla` (physical switches):
```
0: internal id        "Interruptor_BDC_Banco5"
1: display name         (usually same or a longer variant)
2: object class          "InterruptorTecla"
3 (last): "True"/"False" literal (not XML) — switch position
```
**Important for the parser**: `SaveMemoryManager._parse_into_memory`
currently does `if "<?xml" not in last and "&lt;" not in last: continue` —
i.e. it silently skips every type-C object today. That's correct (there's
nothing to decode), but it means NSM's manual-XML-editor object list only
ever shows type-B objects. If a future feature wants to toggle switches by
name, it'll need a separate code path that treats the last pipe field as a
plain string, not an XML blob.

---
## 4. Player / progression data

**`JUGADOR`** (physical player state — health, position, radiation) does
**not** contain money/level/experience. Relevant fields:
```
Salud, RadiacionCuerpo, RadiacionTraje,
Posicion/PosLocal/PosWorld (3D vectors),
Rotacion/CamRotacion (quaternion + eulerAngles),
EnSector (e.g. "SALA_CONTROL"), TRAJE_LlevaPuesto, TRAJE_Nombre,
_esEnvenenado, Avatar
```

**`LOGROS_MLIBRE`** ("Free Mode achievements/progress") is where the
player's actual stats live — this is what `set_simple_stats` and the
inspection script both correctly target:
```
Puntos           — current money (float, can be scientific notation)
NuevoPuntos      — appears to mirror Puntos (kept in sync)
Experiencia      — XP (float)
Nivel            — level (int, observed capped at 100 in every real save —
                   confirms the game itself enforces a 100 cap, matching
                   NSM's existing clamp)
Contadores       — list of CContador {Tipo, Cantidad, IsCumplido} — per-
                   achievement counters (e.g. INICIO_SALA_SUMINISTRO,
                   INICIO_SALA_NUCLEO), not modified by current cheats
```
Note the *observed* level cap of 100 and the presence of anomalous data in
`00066` (Nivel serialized as `1000000000.0`, a float, in a field the game
normally treats as an int-like value ≤100) suggests `00066` was produced
by a **different/older tool or a raw hand-edit** that wrote `Nivel` as a
plain float without the game's own clamp — worth flagging as a case NSM's
own `set_simple_stats` avoids by clamping to `[1, 100]` before writing.

---
## 5. `DISTRIBUCION_INTERNA_FLUIDOS` — the fluid network

```
<CSaveClass>
  <Activo>true</Activo>
  <Tubos>                        — pipes, 205 observed in a built-out plant
    <SSave>
      <Nombre>...</Nombre>                    e.g. "BORO_Water_Pipe_08"
      <Capacidad>100</Capacidad>
      <Integridad>100</Integridad>
      <PerdidaEfectivaDeFluido>0</...>
      <Propulsion>0</Propulsion>
      <Contenido>
        <_liquidos>                — up to 105 <Liquido> entries
          <Liquido><Tipo>AGUA|BORO|FUEL|...</Tipo><Cantidad>N</Cantidad><Radioactivo>bool</Radioactivo></Liquido>
          ...
        <_gases>                   — <Gas> entries, same shape as Liquido, Tipo=VAPOR observed
        <_presiones> (usually empty)
        <Temperatura>0</Temperatura>
        <Propulsion>0</Propulsion>
      <OxidoPresente>false</OxidoPresente>
  <Contenedores>                  — tanks/reservoirs, 34 observed
    <SSaveContenedores>
      <Contenido> ... (same shape as above) ...
      <Name>...</Name>                         e.g. "BC_2_GENERADOR_CIRCULACION"
      <Regulador>, <_flujoPorCiclo>{Entrada,Salida,TemperaturaEntrada}</...>
      <Integridad>, <Aplicados> (list of <Tipos> tags e.g. AUMENTO_POTENCIA),
      <Deterioros>,
      several _promediarX / _lastX / EqVapor history-graph blocks (20-float
      ring buffers + _puntero/_habilitado/_ultimoValor — cosmetic, same
      pattern as §1's ring buffers),
      <_presion>, <_vacuum>, <_tasaFlujoCondensacion>, <_tasaFlujoExtraccion>
```
`flood_reserves` already correctly walks `.//Liquido` and matches on
`Tipo == "AGUA"` / `"BORO"`, setting `Cantidad`. Confirmed this covers both
`Tubos/*/Contenido/_liquidos` and `Contenedores/*/Contenido/_liquidos`
since `findall(".//Liquido")` is unscoped. Other liquid/gas types seen in
the data worth exposing later: `FUEL` (in containers), `VAPOR` (gas).

---
## 6. Other top-level nodes (lower priority, catalogued for completeness)

- **`GESTION_INTERNA_ENERGIA`** — internal energy-analysis snapshot
  (`_resultado`/`_analizando`: lists of human-readable `"Component: N kW"`
  strings — display-only, not safe/useful to edit programmatically).
- **`AMBIENTE`** — world/weather/day-night clock: `Dias`, `Hora`, `Minuto`
  (the in-game timestamp used in the save-reconstruction analysis),
  `Predicciones` (10-day weather forecast), `PotenciaMaximaInstalada` (MW
  capacity — this is what differed between the two playthroughs, 400 vs
  1680), `Poblacion`.
- **`HISTORIA`** — mission/objective/story-progress tracker: current
  `CObjetivo`/`CMision` trees, billing (`Facturas` — utility bills,
  `AutoPagarFacturas`), `Licencias`, difficulty (`NivelDificultad`),
  lifetime stats (`Stat_Energy_Generated`, `LOGRO_ANALIZADOR_Contador`,
  `LOGRO_REPARADOR_Contador`).
- **`ESCENARIO_LOCURA_AO`** — the "AO goes rogue" horror-scenario state
  machine (`Activo`, `Fase`, sabotage counters) — normally all-false/zero
  unless that scenario has been triggered.
- **`EVENTOS`** — one-shot tutorial/"have you seen this yet" boolean flags
  (`TIP_AYUDA_*`, `Primer*`) — no gameplay effect, safe to ignore.
- **`LOGROS_MLIBRE`** — see §4.
- **`LOGISTICA`** — the supply-order/delivery system: `_pedidosSalvados`
  list of `CSavePedido` (order id, timestamps, `Estado`
  `ENTREGADO`/..., `Productos` list of `CProductoSalvado` {Elemento,
  Subclase, Cantidad}). A future "instant delivery" or "free supplies"
  cheat would inject/modify entries here.
- **`INTERFAZ`** — just `Ayuda_UI_Mostrados` (which help popups were shown).
- **`MANTENIMIENTO`** — the maintenance-task list: `Elementos` (up to 208
  observed) of `CElemento` {Tipo (matches a component tag, e.g. `NUCLEO`,
  `BARRAS_DE_CONTROL`), `ObjetoName`, `Tiempo`/`Costo` (repair job time &
  price), `Integridad`, `Desgaste` (wear %), `Desalineado`, `Oxido`,
  `PresenciaYodo`/`PresenciaXenon`, `Contaminado`, `RequiereMantenimiento`,
  `MotivoDelFallo`}. `BARRAS_DE_CONTROL` entries additionally carry
  `DatosBDC` (72× `CDatosBDC` — one per control rod, `IdBanco`/`Id`/
  `Integridad`/`Capacidad`). **This is a second, more granular source of
  per-part wear/integrity data that `repair_all_objects` does not
  currently touch** — worth targeting in a future, more thorough repair
  cheat (it duplicates/tracks the same wear the components report, but at
  finer granularity, e.g. individual control rods).
- **`AUDITORIAS`** — compliance-audit counters (`Resultado_NoCumple*`).
- **`CONFIG_ALARMAS`** — the alarm threshold config: 69× `CConfigAlarmas`
  {`Sector`, `Alarma` (code like `AL002`), `Minimo`/`Maximo`/`Actual`,
  `Descripcion`, `IsActiva`}. A "disable all alarms" or "widen all alarm
  thresholds" cheat would live here.
- **`TRANSFORMACION`** — grid tie-in / external power sale state
  (`EnergiaEntregadaExterior`, `IsEntregandoAlExterior`).
- **`COMUNICACIONES`** — in-game messaging/dispatch state (mission-control
  chatter, operation start/end windows, `PenalizadoPorFaltaSuministroEnCiclo`
  — a supply-shortfall penalty flag worth exposing as a cheat toggle).
- **`QUIMICOS_CMOTOR`** — boron-dosing pump component, same `Base` pattern.
- **`CORTES_PROGRAMADOS`** — scheduled power outages list (empty in all
  observed saves).

---
## 7. Chronology cross-check (why this matters for testing)

The 7 saves used for this analysis, ordered by **in-game** day (not file
mtime — file mtime and in-game day diverge because two separate games were
saved into overlapping slot ranges):

| Save | In-game Day/Time | Reactor | Money | Notes |
|---|---|---|---|---|
| 00066 | Day 0, 9:52 | NOREACTIVO | 1,000,000,000.0 (exact) | fresh/new game, 400 MW installed, different XML header |
| 00065 | Day 5, 5:41 | NOREACTIVO, Xenon=580 | 844,053,056 | just tripped, xenon-poisoned |
| 00061 | Day 16, 5:30 | NOREACTIVO | 1.1535e9 | cold start of the "main" playthrough |
| 00062 | Day 16, 7:04 | REACTIVO | 1.1535e9 | reactor just started, 15 min after 00061 |
| 00063 | Day 18, 23:07 | REACTIVO | 1.1803e9 | |
| AUTOSAVE | Day 20, 5:39 | REACTIVO | 1.2252e9 | auto-save 17s before 00064 |
| 00064 | Day 20, 5:44 | REACTIVO | 1.2252e9 | manual save right after the autosave |

This gives a good regression set: 00066/00065 exercise the "reactor
off"/"just tripped, xenon nonzero" paths, 00062-00064/AUTOSAVE exercise the
"reactor running, growing wear" path — useful fixtures once there's a test
suite.

---
## 8. Implications for NSMDB redesign (for the next round of work)

Things worth reconsidering now that the real format is confirmed:
1. **Drop or de-prioritize the `_valoresFloat`/etc. dict-container lookup**
   (§1) — it never matches real data; `find(f".//{key}")` direct lookup is
   the only path that's been confirmed to work.
2. **`.nsmdb` currently keys entries by bare XML tag name** (`NUCLEO`,
   `PRESURIZADOR`, ...) and applies them with an unscoped `find(f".//{key}")`
   search inside that component's `inner_xml`. Given how deeply nested some
   real fields are (e.g. `CONTROL_NEW_CORE/_bancos/CBancoSaveClass/_cantidadBarras`,
   or per-bay/per-rod data), a bare tag-name key is ambiguous the moment two
   different nested structs reuse a field name (e.g. `Integridad` appears
   at 15+ different nesting depths across the file). Right now this mostly
   works by luck (`find` returns the *first* match in document order). A
   redesigned `.nsmdb` might want an explicit path-like key
   (`CONTROL_NEW_CORE/_bahias/*/IdCombustible` or similar) for anything
   beyond the current flat top-level fields.
3. **Repeated/indexed sub-structures** (the 9 control-rod banks/bays, the
   9 battery racks, the N diesel generators, the 205 pipes, the 34
   containers, the 208 maintenance elements) are exactly where "apply to
   every element of a list" cheats belong — `max_backup_generators` and
   `flood_reserves` already do this correctly with `findall`. Any new
   per-list cheat (reseal all fuel rods, reset all alarm thresholds,
   refill all diesel Combustible, un-contaminate everything) can follow
   the same `findall(".//Tag")` + loop pattern.
4. **`objetos` type-C switches** (§3) are not currently loaded into memory
   at all — extending the manual editor / a new cheat to toggle them needs
   a new code path since they don't carry an XML payload.
5. **`MANTENIMIENTO/Elementos`** is a second, finer-grained wear-tracking
   system parallel to the per-component `Integridad`/`Desgaste` fields;
   `repair_all_objects` only touches the latter today, so a save can show
   "0 outstanding maintenance" per-component while `MANTENIMIENTO` still
   lists pending jobs, or vice versa — worth reconciling.
